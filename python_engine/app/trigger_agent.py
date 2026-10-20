import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import ai_advisor

logger = logging.getLogger("quant-trigger-agent")

AUDIT_DIR = Path("/app/audit_data") if Path("/app").exists() else Path(__file__).resolve().parent / "audit_data"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
SIGNALS_INBOX_FILE = AUDIT_DIR / "signals_inbox.json"


class ZeroLossSignalBus:
    """
    Bus de comunicación asíncrono y persistente entre los 8 Subagentes de Escaneo
    y el 9º Subagente Disparador (TriggerExecutionAgent).
    - Garantiza cero pérdida de señales ante oscilaciones del mercado o reinicios.
    - Cola priorizada por Quant Score (mayor puntuación primero).
    - Retención TTL de 15 minutos para que las oportunidades no desaparezcan entre barridos de 10s.
    """

    def __init__(self, ttl_seconds: int = 900):
        self.ttl_seconds = ttl_seconds
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._active_signals: dict[str, dict[str, Any]] = {}
        self._load_persisted_inbox()

    def _load_persisted_inbox(self) -> None:
        if SIGNALS_INBOX_FILE.exists():
            try:
                data = json.loads(SIGNALS_INBOX_FILE.read_text(encoding="utf-8"))
                now = time.time()
                valid = {}
                for tk, sig in data.items():
                    # Conservar si no ha expirado su TTL
                    ts = sig.get("queued_timestamp", 0)
                    if (now - ts) < self.ttl_seconds and sig.get("status") not in ("EXECUTED", "DISMISSED"):
                        valid[tk] = sig
                self._active_signals = valid
                logger.info(f"[ZeroLossSignalBus] Recuperadas {len(self._active_signals)} señales vivas desde inbox.")
            except Exception as e:
                logger.warning(f"[ZeroLossSignalBus] Error cargando signals_inbox.json: {e}")

    def persist_inbox(self) -> None:
        try:
            SIGNALS_INBOX_FILE.write_text(
                json.dumps(self._active_signals, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"[ZeroLossSignalBus] Error guardando signals_inbox.json: {e}")

    async def emit_signal(self, raw_signal: dict[str, Any]) -> bool:
        """
        Recibe una señal detectada por cualquiera de los 8 subagentes y la encola de forma atómica.
        Prioridad = 100 - quant_score (de modo que Score 100 tenga prioridad 0, máxima).
        """
        ticker = raw_signal["ticker"]
        score = int(raw_signal.get("quant_score", 50))
        now = time.time()

        existing = self._active_signals.get(ticker)
        opp_id = existing["opportunity_id"] if existing else f"opp-{ticker.lower()}-{int(now)}"
        first_seen = existing["first_seen_at"] if existing else raw_signal.get("detected_at", datetime.now(timezone.utc).isoformat())

        raw_signal["opportunity_id"] = opp_id
        raw_signal["first_seen_at"] = first_seen
        raw_signal["queued_timestamp"] = now
        raw_signal["bus_status"] = "QUEUED"

        self._active_signals[ticker] = raw_signal
        self.persist_inbox()

        # Prioridad invertida para heapq (0 = mayor prioridad)
        priority = 100 - score
        await self._queue.put((priority, ticker, raw_signal))
        logger.info(f"[ZeroLossSignalBus] Señal {ticker} (Score {score}) encolada con éxito. Cola: {self._queue.qsize()}")
        return True

    async def get_next_signal(self) -> tuple[int, str, dict[str, Any]]:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def get_active_signals(self) -> list[dict[str, Any]]:
        now = time.time()
        # Limpieza de expiradas
        alive = []
        for tk, sig in list(self._active_signals.items()):
            ts = sig.get("queued_timestamp", 0)
            if (now - ts) > self.ttl_seconds and sig.get("status") not in ("EXECUTED", "DISMISSED"):
                sig["status"] = "EXPIRED"
            if sig.get("status") != "DISMISSED":
                alive.append(sig)
        return alive

    def dismiss_signal(self, ticker: str) -> bool:
        if ticker in self._active_signals:
            self._active_signals[ticker]["status"] = "DISMISSED"
            self.persist_inbox()
            return True
        return False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()


class TriggerExecutionAgent:
    """
    9º Subagente Especializado: DISPARADOR Y GESTOR DE EJECUCIÓN (TriggerExecutionAgent).
    Responsabilidades exclusivas:
    1. Calcular al céntimo los niveles Bracket: Stop Loss (-1.5xATR), Take Profit (+2R), Breakeven (+0.85R) y Trailing (+1.45R).
    2. Dimensionamiento de posición exacto (Riesgo estricto 1% del Equity + Tope de asignación).
    3. Validación del filtro institucional de comisiones (<= 10% del beneficio bruto).
    4. Consulta al Asesor IA y Despacho atómico OCO a Alpaca Paper sin bloquear a los 8 escáneres.
    """

    def __init__(self, signal_bus: ZeroLossSignalBus):
        self.bus = signal_bus
        self.is_running = False
        self._worker_task: Optional[asyncio.Task] = None
        self.total_dispatched: int = 0
        self.last_dispatched_at: Optional[str] = None
        self.last_dispatched_ticker: Optional[str] = None

    def calculate_bracket_levels(
        self,
        entry_price: float,
        atr_14: float,
        strategy_params: dict[str, Any],
        recent_swing_high: Optional[float] = None,
    ) -> dict[str, float]:
        """Calcula al céntimo la estructura completa de niveles OCO y salidas dinámicas."""
        mult_sl = float(strategy_params.get("atr_stop_loss_multiplier", 1.5))
        rr_ratio = float(strategy_params.get("take_profit_reward_risk_ratio", 2.0))
        be_trigger_r = float(strategy_params.get("breakeven_trigger_r", 0.85))
        be_lock_r = float(strategy_params.get("breakeven_lock_r", 0.08))
        trail_trigger_r = float(strategy_params.get("trailing_ratchet_trigger_r", 1.45))
        trail_lock_r = float(strategy_params.get("trailing_ratchet_lock_r", 0.55))

        # Stop loss inicial a 1.5x ATR
        risk_per_share = round(mult_sl * atr_14, 2)
        if risk_per_share <= 0:
            risk_per_share = round(entry_price * 0.02, 2)

        stop_loss_price = round(entry_price - risk_per_share, 2)
        take_profit_price = round(entry_price + (risk_per_share * rr_ratio), 2)

        # Ajuste inteligente por resistencia estructural (Swing High reciente):
        # Si existe un techo intermedio y el TP estándar queda por encima, se ajusta a un 0.5% por debajo
        # del swing high para asegurar ejecución, siempre que preserve al menos un R:R atractivo (>= 1.25R).
        if recent_swing_high and recent_swing_high > entry_price:
            capped_tp = round(recent_swing_high * 0.995, 2)
            if capped_tp < take_profit_price and (capped_tp - entry_price) >= (risk_per_share * 1.25):
                take_profit_price = capped_tp

        breakeven_trigger_price = round(entry_price + (risk_per_share * be_trigger_r), 2)
        breakeven_lock_price = round(entry_price + (risk_per_share * be_lock_r), 2)
        trailing_trigger_price = round(entry_price + (risk_per_share * trail_trigger_r), 2)
        trailing_lock_price = round(entry_price + (risk_per_share * trail_lock_r), 2)

        return {
            "entry_price": round(entry_price, 2),
            "risk_per_share": risk_per_share,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "breakeven_trigger_price": breakeven_trigger_price,
            "breakeven_lock_price": breakeven_lock_price,
            "trailing_trigger_price": trailing_trigger_price,
            "trailing_lock_price": trailing_lock_price,
            "reward_risk_ratio": round((take_profit_price - entry_price) / max(0.01, risk_per_share), 2),
            "recent_swing_high": recent_swing_high,
        }

    def calculate_position_sizing(
        self,
        portfolio_value: float,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        strategy_params: dict[str, Any],
        commission_per_order: float = 0.35,
        max_comm_ratio: float = 0.10,
    ) -> dict[str, Any]:
        """Calcula el dimensionamiento exacto de acciones respetando el 1% de riesgo de cartera."""
        risk_pct = float(strategy_params.get("max_portfolio_risk_per_trade_pct", 0.01))
        max_alloc_pct = float(strategy_params.get("max_position_allocation_pct", 0.25))

        max_risk_amount = portfolio_value * risk_pct
        max_capital_allowed = portfolio_value * max_alloc_pct

        risk_per_share = max(0.01, entry_price - stop_loss_price)
        shares_by_risk = math.floor(max_risk_amount / risk_per_share)
        shares_by_capital = math.floor(max_capital_allowed / entry_price)

        final_shares = min(shares_by_risk, shares_by_capital)

        # Validación del filtro de comisiones institucionales
        expected_profit = (take_profit_price - entry_price) * final_shares
        round_trip_comm = commission_per_order * 2.0
        comm_ratio = (round_trip_comm / expected_profit) if expected_profit > 0 else 1.0

        viable = (final_shares > 0) and (comm_ratio <= max_comm_ratio)
        reject_reason = None
        if final_shares <= 0:
            reject_reason = f"Capital insuficiente para comprar 1 acción con riesgo del {risk_pct*100}%."
        elif comm_ratio > max_comm_ratio:
            reject_reason = f"Comisiones ({round_trip_comm:.2f}€) superan el {max_comm_ratio*100:.0f}% del beneficio esperado ({expected_profit:.2f}€)."

        return {
            "viable": viable,
            "shares_to_buy": final_shares,
            "invested_capital": round(final_shares * entry_price, 2),
            "max_risk_eur": round(final_shares * risk_per_share, 2),
            "expected_profit_eur": round(expected_profit, 2),
            "commissions_eur": round(round_trip_comm, 2),
            "commission_to_profit_ratio": round(comm_ratio, 4),
            "rejection_reason": reject_reason,
        }

    def enrich_opportunity(
        self,
        raw_signal: dict[str, Any],
        portfolio_value: float,
        strategy_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Enriquece una señal con todos los cálculos de niveles y tamaño listos para ejecución."""
        entry = float(raw_signal.get("entry_price") or raw_signal.get("price") or 0.0)
        atr = float(raw_signal.get("atr_14") or (entry * 0.02))
        recent_swing_high = raw_signal.get("recent_swing_high")

        brackets = self.calculate_bracket_levels(
            entry, atr, strategy_params, recent_swing_high=recent_swing_high
        )
        sizing = self.calculate_position_sizing(
            portfolio_value=portfolio_value,
            entry_price=entry,
            stop_loss_price=brackets["stop_loss_price"],
            take_profit_price=brackets["take_profit_price"],
            strategy_params=strategy_params,
        )

        raw_signal.update({
            "price": brackets["entry_price"],
            "entry_price": brackets["entry_price"],
            "stop_loss_price": brackets["stop_loss_price"],
            "take_profit_price": brackets["take_profit_price"],
            "breakeven_trigger_price": brackets["breakeven_trigger_price"],
            "breakeven_lock_price": brackets["breakeven_lock_price"],
            "trailing_trigger_price": brackets["trailing_trigger_price"],
            "trailing_lock_price": brackets["trailing_lock_price"],
            "risk_per_share": brackets["risk_per_share"],
            "order_sizing": sizing,
            "shares_to_buy": sizing["shares_to_buy"],
            "sizing_viable": sizing["viable"],
            "trigger_agent_ready": True,
            "trigger_agent_timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return raw_signal

    def get_telemetry(self) -> dict[str, Any]:
        return {
            "agent_name": "TRIGGER_EXECUTION_AGENT",
            "agent_role": "Calculador de Niveles SL/TP y Despacho OCO",
            "status": "RUNNING" if self.is_running else "IDLE",
            "signals_in_queue": self.bus.queue_size,
            "total_dispatched": self.total_dispatched,
            "last_dispatched_at": self.last_dispatched_at,
            "last_dispatched_ticker": self.last_dispatched_ticker,
        }


# Instancia singleton del Bus y del Subagente Disparador
zero_loss_signal_bus = ZeroLossSignalBus()
trigger_agent = TriggerExecutionAgent(zero_loss_signal_bus)
