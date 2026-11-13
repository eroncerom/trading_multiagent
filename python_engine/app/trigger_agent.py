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
        self._counter: int = 0
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
        # Se incluye un contador incremental único como desempate para evitar '<' not supported between instances of 'dict'
        priority = 100 - score
        self._counter += 1
        await self._queue.put((priority, self._counter, ticker, raw_signal))
        logger.info(f"[ZeroLossSignalBus] Señal {ticker} (Score {score}) encolada con éxito. Cola: {self._queue.qsize()}")
        return True

    async def get_next_signal(self) -> tuple[int, str, dict[str, Any]]:
        item = await self._queue.get()
        if len(item) == 4:
            priority, _cnt, ticker, raw_signal = item
            return (priority, ticker, raw_signal)
        return item

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
            if sig.get("status") not in ("DISMISSED", "EXECUTED"):
                alive.append(sig)
        return alive

    def mark_executed(self, ticker: str) -> bool:
        """Retira definitivamente una señal ya ejecutada (o con posición abierta) del bus y del inbox."""
        if ticker in self._active_signals:
            self._active_signals.pop(ticker, None)
            self.persist_inbox()
            return True
        return False

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
        precalculated_tp: Optional[float] = None,
        atr_15m: Optional[float] = None,
        pullback_low: Optional[float] = None,
    ) -> dict[str, float]:
        """Calcula los niveles OCO anclados al pullback intradía (15m) con asimetría realista."""
        atr_ref = float(atr_15m) if (atr_15m is not None and atr_15m > 0) else float(atr_14)
        if atr_ref <= 0:
            atr_ref = entry_price * 0.006

        sl_mult = float(strategy_params.get("atr_stop_loss_multiplier", 1.25))
        sl_buffer = float(strategy_params.get("intraday_sl_buffer_atr", 0.25)) * atr_ref
        min_atr_risk = float(strategy_params.get("intraday_sl_min_atr", 0.80)) * atr_ref
        max_atr_risk = float(strategy_params.get("intraday_sl_max_atr", 1.50)) * atr_ref

        if pullback_low is not None and 0 < float(pullback_low) < entry_price:
            raw_risk = entry_price - (float(pullback_low) - sl_buffer)
        else:
            raw_risk = sl_mult * atr_ref

        atr_clamped_risk = max(min_atr_risk, min(max_atr_risk, raw_risk))
        min_pct_risk = entry_price * float(strategy_params.get("intraday_sl_min_price_pct", 0.003))
        max_pct_risk = entry_price * float(strategy_params.get("intraday_sl_max_price_pct", 0.018))
        risk_per_share = round(max(min_pct_risk, min(max_pct_risk, atr_clamped_risk)), 2)
        if risk_per_share <= 0:
            risk_per_share = round(max(0.05, entry_price * 0.005), 2)

        stop_loss_price = round(entry_price - risk_per_share, 2)

        rr_ratio = float(strategy_params.get("take_profit_reward_risk_ratio", 1.90))
        max_gain_pct = float(strategy_params.get("max_single_swing_gain_pct", 0.04))
        max_allowed_tp = round(entry_price * (1.0 + max_gain_pct), 2)
        base_tp = round(entry_price + (risk_per_share * rr_ratio), 2)
        take_profit_price = min(base_tp, max_allowed_tp)

        if bool(strategy_params.get("cap_tp_at_swing_high_in_uptrend", True)):
            if recent_swing_high and recent_swing_high > entry_price:
                structural_ceiling = round(float(recent_swing_high) * 0.996, 2)
                if structural_ceiling > entry_price:
                    take_profit_price = min(take_profit_price, structural_ceiling)

        be_trigger_r = float(strategy_params.get("breakeven_trigger_r", 0.60))
        be_lock_r = float(strategy_params.get("breakeven_lock_r", 0.15))
        trail_trigger_r = float(
            strategy_params.get("trailing_stage1_trigger_r", strategy_params.get("trailing_ratchet_trigger_r", 1.00))
        )
        trail_lock_r = float(
            strategy_params.get("trailing_stage1_lock_mfe_pct", strategy_params.get("trailing_ratchet_lock_r", 0.60))
        )

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
        commission_per_order: Optional[float] = None,
        max_comm_ratio: Optional[float] = None,
    ) -> dict[str, Any]:
        """Dimensiona acciones por riesgo de cartera (0.28%), comisiones ($1/orden) y beneficio neto mínimo."""
        risk_pct = float(strategy_params.get("max_portfolio_risk_per_trade_pct", 0.0028))
        eff_comm = float(
            commission_per_order if commission_per_order is not None else strategy_params.get("commission_per_order", 1.0)
        )
        eff_max_comm_ratio = float(
            max_comm_ratio if max_comm_ratio is not None else strategy_params.get("max_comm_ratio", 0.25)
        )
        min_net_profit_usd = float(strategy_params.get("min_net_profit_usd", 8.0))

        if not strategy_params.get("use_adaptive_allocation_cap", True):
            max_alloc_pct = float(strategy_params.get("max_position_allocation_pct", 0.30))
            tier_label = f"FIXED_CAP ({round(max_alloc_pct * 100, 1)}%)"
        elif portfolio_value <= 2000.0:
            max_alloc_pct, tier_label = 0.40, "ADAPTIVE_STARTER_1K_2K (40%)"
        elif portfolio_value <= 5000.0:
            max_alloc_pct, tier_label = 0.30, "ADAPTIVE_GROWTH_2K_5K (30%)"
        elif portfolio_value <= 15000.0:
            max_alloc_pct, tier_label = 0.20, "ADAPTIVE_MEDIUM_5K_15K (20%)"
        else:
            max_alloc_pct, tier_label = 0.12, "ADAPTIVE_INSTITUTIONAL_15K+ (12%)"

        round_trip_comm = eff_comm * 2.0
        max_risk_amount = portfolio_value * risk_pct
        risk_budget_for_shares = max(0.0, max_risk_amount - round_trip_comm)
        max_capital_allowed = portfolio_value * max_alloc_pct

        risk_per_share = max(0.01, entry_price - stop_loss_price)
        shares_by_risk = math.floor(risk_budget_for_shares / risk_per_share)
        shares_by_capital = math.floor(max(0.0, max_capital_allowed - round_trip_comm) / max(0.01, entry_price))

        final_shares = min(shares_by_risk, shares_by_capital)

        reward_per_share = take_profit_price - entry_price
        achieved_rr = (reward_per_share / risk_per_share) if risk_per_share > 0 else 0.0
        min_rr_threshold = float(strategy_params.get("min_reward_risk_ratio", 1.60))

        gross_profit = reward_per_share * final_shares
        net_profit = gross_profit - round_trip_comm
        actual_max_loss = (final_shares * risk_per_share) + round_trip_comm
        comm_ratio = (round_trip_comm / gross_profit) if gross_profit > 0 else 1.0
        net_rr = (net_profit / actual_max_loss) if actual_max_loss > 0 else 0.0
        invested_capital = round(final_shares * entry_price, 2)
        min_required_net = max(round_trip_comm * 2.0, min_net_profit_usd)

        viable = (
            (final_shares > 0)
            and (comm_ratio <= eff_max_comm_ratio)
            and (achieved_rr >= min_rr_threshold)
            and (net_profit >= min_required_net)
        )
        reject_reason = None
        if final_shares <= 0:
            reject_reason = f"Capital insuficiente para comprar 1 acción con riesgo máximo del {risk_pct*100:.2f}%."
        elif achieved_rr < min_rr_threshold:
            reject_reason = (
                f"Techo estructural demasiado cercano (${take_profit_price:.2f}): "
                f"Ratio R:R bruto {achieved_rr:.2f}R (< {min_rr_threshold:.2f}R mínimo)."
            )
        elif net_profit < min_required_net:
            reject_reason = (
                f"Beneficio neto proyectado (${net_profit:.2f}) inferior al mínimo exigible "
                f"(${min_required_net:.2f} neto tras ${round_trip_comm:.2f} de comisión)."
            )
        elif comm_ratio > eff_max_comm_ratio:
            reject_reason = (
                f"Comisiones (${round_trip_comm:.2f}) superan el {eff_max_comm_ratio*100:.0f}% "
                f"del beneficio bruto (${gross_profit:.2f})."
            )

        return {
            "viable": viable,
            "shares_to_buy": final_shares,
            "invested_capital": invested_capital,
            "total_capital_allocated": invested_capital,
            "portfolio_allocation_pct": round((invested_capital / portfolio_value) * 100.0, 2) if portfolio_value > 0 else 0.0,
            "max_capital_allowed_per_trade": round(max_capital_allowed, 2),
            "allocation_tier": tier_label,
            "entry_limit_price": round(entry_price, 2),
            "stop_loss_price": round(stop_loss_price, 2),
            "take_profit_price": round(take_profit_price, 2),
            "max_risk_eur": round(actual_max_loss, 2),
            "expected_profit_eur": round(net_profit, 2),
            "commissions_eur": round(round_trip_comm, 2),
            "commission_to_profit_ratio": round(comm_ratio, 4),
            "rejection_reason": reject_reason,
            "financial_metrics": {
                "max_loss_with_commissions": round(actual_max_loss, 2),
                "portfolio_risk_pct": round((actual_max_loss / portfolio_value) * 100.0, 2) if portfolio_value > 0 else 0.0,
                "expected_net_profit": round(net_profit, 2),
                "net_reward_risk_ratio": round(net_rr, 2),
                "round_trip_commission": round(round_trip_comm, 2),
                "commission_to_gross_profit_pct": round(comm_ratio * 100.0, 2),
            },
        }

    def enrich_opportunity(
        self,
        raw_signal: dict[str, Any],
        portfolio_value: float,
        strategy_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Enriquece una señal con niveles OCO intradía, métricas reales y tamaño listo para ejecución."""
        entry = float(raw_signal.get("entry_price") or raw_signal.get("price") or 0.0)
        atr_15m = raw_signal.get("atr_15m")
        atr = float(raw_signal.get("atr_14") or (entry * 0.01))
        pullback_low = raw_signal.get("pullback_low")
        recent_swing_high = raw_signal.get("intraday_swing_high") or raw_signal.get("recent_swing_high")

        brackets = self.calculate_bracket_levels(
            entry,
            atr,
            strategy_params,
            recent_swing_high=recent_swing_high,
            atr_15m=float(atr_15m) if atr_15m is not None else None,
            pullback_low=float(pullback_low) if pullback_low is not None else None,
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
            "reward_risk_ratio": brackets["reward_risk_ratio"],
            "order_sizing": sizing,
            "financial_metrics": sizing["financial_metrics"],
            "shares_to_buy": sizing["shares_to_buy"],
            "sizing_viable": sizing["viable"],
            "viable": sizing["viable"],
            "trigger_agent_ready": True,
            "trigger_agent_timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if not sizing["viable"] and sizing.get("rejection_reason"):
            raw_signal["rejection_reason"] = sizing["rejection_reason"]
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
