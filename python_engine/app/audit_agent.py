import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("quant-audit-agent")

AUDIT_DIR = Path("/app/audit_data") if Path("/app").exists() else Path("python_engine/app/audit_data")
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

TRADES_LEDGER_FILE = AUDIT_DIR / "trades_ledger.jsonl"
SESSIONS_FILE = AUDIT_DIR / "sessions_history.json"
REPORTS_FILE = AUDIT_DIR / "audit_reports.json"
STRATEGY_PARAMS_FILE = AUDIT_DIR / "strategy_params.json"


DEFAULT_CLUSTERS: dict[str, list[str]] = {
    "TECH_GROWTH": [
        "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL",
        "META", "TSLA", "NFLX", "PLTR", "AMD",
        "AVGO", "CRM", "UBER", "INTC", "CSCO",
    ],
    "FINANCE_CONSUMER": [
        "JPM", "BAC", "WFC", "GS", "MS",
        "V", "MA", "PYPL", "KO", "PEP",
        "COST", "WMT", "NKE", "MCD", "DIS",
    ],
    "HEALTH_INDUSTRIAL": [
        "JNJ", "PFE", "ABBV", "MRK", "LLY",
        "UNH", "CAT", "DE", "GE", "HON",
        "BA", "LMT", "XOM", "CVX", "UPS",
    ],
    "HIGH_BETA_EXPANSION": [
        "SOFI", "COIN", "HOOD", "SHOP", "SNOW",
        "CRWD", "PANW", "NET", "SQ", "AFRM",
        "SMCI", "ARM", "RIVN", "DKNG", "MARA",
    ],
}

DEFAULT_STRATEGY_PARAMS: dict[str, Any] = {
    "version": 7,
    "stoch_k_window": 14,
    "stoch_d_smooth": 4,
    "adx_window": 14,
    "ema_fast_window": 20,
    "sma_mid_window": 50,
    "sma_window": 200,
    "rsi_window": 14,
    "atr_window": 14,
    "adx_bullish_min": 19.0,
    "adx_exhaustion_max": 35.0,
    "adx_lateral_max": 20.0,
    "stoch_bullish_oversold": 36.0,
    "stoch_lateral_oversold": 20.0,
    "anti_falling_knife_enabled": True,
    "max_bearish_body_atr_for_entry": 0.80,
    "min_close_location_value": 0.25,
    "min_quant_score": 55,
    "atr_stop_loss_multiplier": 1.5,
    "endogenous_atr_scaling_enabled": True,
    "min_atr_volatility_pct": 1.80,
    "min_kaufman_efficiency_ratio": 0.14,
    "min_relative_volume_rvol": 0.60,
    "min_momentum_20d_pct": -2.0,
    "enable_breakeven_protection": True,
    "breakeven_trigger_r": 0.85,
    "breakeven_lock_r": 0.08,
    "trailing_ratchet_trigger_r": 1.45,
    "trailing_ratchet_lock_r": 0.55,
    "take_profit_reward_risk_ratio": 2.0,
    "max_portfolio_risk_per_trade_pct": 0.01,
    "use_adaptive_allocation_cap": True,
    "max_position_allocation_pct": 0.25,
    "virtual_portfolio_base": 5000.0,
    "max_session_drawdown_pct": 0.04,
    "enable_lateral_regime_trades": True,
    "macro_regime_gatekeeper_enabled": True,
    "macro_benchmark_tickers": ["SPY", "QQQ"],
    "macro_index_threshold_drop_pct": -0.75,
    "macro_qqq_threshold_drop_pct": -1.00,
    "macro_caution_threshold_drop_pct": -0.40,
    "macro_intraday_vwap_filter": True,
    "macro_market_breadth_min_advancing_pct": 0.38,
    "macro_cooldown_minutes_after_reversal": 30,
    "clusters": DEFAULT_CLUSTERS,
    "last_updated_at": None,
    "last_updated_by": "SYSTEM_DEFAULT",
}


def get_adaptive_allocation_pct(portfolio_value: float, params: dict[str, Any]) -> tuple[float, str]:
    """
    Calcula el tope máximo de capital invertido por operación adaptándose al tamaño de la cartera:
    - Carteras de inicio (1.000€ - 2.000€): 35% máx por compra (hasta 3 posiciones, supera filtro comisiones).
    - Carteras en crecimiento (2.001€ - 5.000€): 25% máx por compra (hasta 4 posiciones).
    - Carteras intermedias (5.001€ - 15.000€): 15% máx por compra (hasta 6 posiciones).
    - Carteras consolidadas (> 15.000€): 10% máx por compra (estándar institucional).
    Siempre subordinado a que la pérdida si toca el Stop Loss jamás supere el 1% de la cartera total.
    """
    if not params.get("use_adaptive_allocation_cap", True):
        fixed = float(params.get("max_position_allocation_pct", 0.35))
        return fixed, f"FIXED_CAP ({round(fixed * 100, 1)}%)"

    if portfolio_value <= 2000.0:
        return 0.35, "ADAPTIVE_STARTER_1K_2K (35%)"
    elif portfolio_value <= 5000.0:
        return 0.25, "ADAPTIVE_GROWTH_2K_5K (25%)"
    elif portfolio_value <= 15000.0:
        return 0.15, "ADAPTIVE_MEDIUM_5K_15K (15%)"
    else:
        return 0.10, "ADAPTIVE_INSTITUTIONAL_15K+ (10%)"


class IndependentAuditAgent:
    """
    Agente Independiente de Registro Operativo y Auditoría Crítica Cuantitativa.
    - Registra cada sesión, cada operación ejecutada (con el snapshot técnico completo de entrada/salida)
      y las señales descartadas en disco (JSONL/JSON) para trazabilidad total.
    - Supervisa el estado de las órdenes/posiciones en Alpaca para conciliar resultados reales (TP, SL, PnL).
    - Al finalizar una sesión (o activarse el cortafuegos del 4% de pérdida), ejecuta una auditoría
      crítica detectando fallos estructurales (barridos prematuros de SL, bajo desempeño en rango lateral,
      exceso de slippage, activos tóxicos) y genera ajustes concretos de parámetros para iterar y corregir.
    """

    def __init__(self) -> None:
        self.strategy_params: dict[str, Any] = self._load_strategy_params()
        self.current_session: Optional[dict[str, Any]] = None
        self.trades_cache: list[dict[str, Any]] = self._load_trades_ledger()
        self.audit_reports: list[dict[str, Any]] = self._load_json_list(REPORTS_FILE)
        self.sessions_history: list[dict[str, Any]] = self._load_json_list(SESSIONS_FILE)

    def _load_strategy_params(self) -> dict[str, Any]:
        if STRATEGY_PARAMS_FILE.exists():
            try:
                data = json.loads(STRATEGY_PARAMS_FILE.read_text(encoding="utf-8"))
                merged = {**DEFAULT_STRATEGY_PARAMS, **data}
                return merged
            except Exception as exc:
                logger.warning(f"Error cargando strategy_params.json: {exc}")
        return dict(DEFAULT_STRATEGY_PARAMS)

    def save_strategy_params(self, new_params: dict[str, Any], updated_by: str = "AUDIT_AGENT") -> dict[str, Any]:
        self.strategy_params.update(new_params)
        self.strategy_params["version"] = int(self.strategy_params.get("version", 1)) + 1
        self.strategy_params["last_updated_at"] = datetime.now(timezone.utc).isoformat()
        self.strategy_params["last_updated_by"] = updated_by
        try:
            STRATEGY_PARAMS_FILE.write_text(
                json.dumps(self.strategy_params, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error(f"No se pudo guardar strategy_params.json: {exc}")
        return self.strategy_params

    def reset_strategy_params(self) -> dict[str, Any]:
        self.strategy_params = dict(DEFAULT_STRATEGY_PARAMS)
        self.strategy_params["last_updated_at"] = datetime.now(timezone.utc).isoformat()
        self.strategy_params["last_updated_by"] = "MANUAL_RESET"
        STRATEGY_PARAMS_FILE.write_text(
            json.dumps(self.strategy_params, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.strategy_params

    def _load_trades_ledger(self) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        if not TRADES_LEDGER_FILE.exists():
            return trades
        try:
            for line in TRADES_LEDGER_FILE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    trades.append(json.loads(line))
        except Exception as exc:
            logger.warning(f"Error leyendo trades_ledger.jsonl: {exc}")
        return trades

    def _rewrite_trades_ledger(self) -> None:
        try:
            lines = [json.dumps(t, ensure_ascii=False) for t in self.trades_cache]
            TRADES_LEDGER_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except Exception as exc:
            logger.error(f"Error guardando trades_ledger.jsonl: {exc}")

    def _load_json_list(self, path: Path) -> list[dict[str, Any]]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception as exc:
                logger.warning(f"Error cargando {path.name}: {exc}")
        return []

    def _save_json_list(self, path: Path, data: list[dict[str, Any]]) -> None:
        try:
            path.write_text(json.dumps(data[:200], indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error(f"Error escribiendo {path.name}: {exc}")

    # -----------------------------------------------------------------
    # GESTIÓN DE SESIÓN Y CORTAFUEGOS DEL 4%
    # -----------------------------------------------------------------

    def start_session(
        self,
        execution_mode: str,
        initial_equity: float,
        max_drawdown_pct: float,
        interval_seconds: int,
        session_duration_minutes: Optional[int],
        tickers: list[str],
    ) -> dict[str, Any]:
        session_id = f"sess-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        hard_stop_equity = round(initial_equity * (1.0 - max_drawdown_pct), 2)
        self.current_session = {
            "session_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "execution_mode": execution_mode,
            "interval_seconds": interval_seconds,
            "session_duration_minutes": session_duration_minutes,
            "tickers": tickers,
            "initial_equity": round(initial_equity, 2),
            "current_equity": round(initial_equity, 2),
            "peak_equity": round(initial_equity, 2),
            "lowest_equity": round(initial_equity, 2),
            "hard_stop_equity_4pct": hard_stop_equity,
            "max_drawdown_limit_pct": round(max_drawdown_pct * 100, 2),
            "current_drawdown_From_initial_pct": 0.0,
            "max_observed_drawdown_pct": 0.0,
            "session_pnl_usd": 0.0,
            "session_pnl_pct": 0.0,
            "circuit_breaker_triggered": False,
            "stop_reason": None,
            "scans_executed": 0,
            "opportunities_detected": 0,
            "trades_opened": 0,
            "strategy_params_snapshot": dict(self.strategy_params),
        }
        logger.info(
            f"[AUDIT AGENT] Nueva sesión registrada: {session_id} | Modo={execution_mode} | "
            f"Equity Inicial=${initial_equity:,.2f} | Cortafuegos 4% en ${hard_stop_equity:,.2f}"
        )
        return self.current_session

    def update_session_equity(
        self,
        live_equity: float,
        scans_executed: int,
        opportunities_detected_delta: int = 0,
    ) -> dict[str, Any]:
        """
        Actualiza el equity en vivo de la sesión y evalúa si se ha alcanzado el límite del 4% de pérdida.
        Devuelve dict con {"breached_4pct": bool, "drawdown_pct": float, ...}
        """
        if not self.current_session:
            return {"breached_4pct": False, "drawdown_pct": 0.0}

        initial_eq = float(self.current_session["initial_equity"])
        limit_pct = float(self.current_session["max_drawdown_limit_pct"]) / 100.0

        self.current_session["current_equity"] = round(live_equity, 2)
        self.current_session["peak_equity"] = round(
            max(float(self.current_session["peak_equity"]), live_equity), 2
        )
        self.current_session["lowest_equity"] = round(
            min(float(self.current_session["lowest_equity"]), live_equity), 2
        )
        self.current_session["scans_executed"] = scans_executed
        self.current_session["opportunities_detected"] = (
            int(self.current_session.get("opportunities_detected", 0)) + opportunities_detected_delta
        )

        pnl_usd = round(live_equity - initial_eq, 2)
        pnl_pct = round((pnl_usd / initial_eq) * 100.0, 3) if initial_eq > 0 else 0.0
        drawdown_from_initial = max(0.0, (initial_eq - live_equity) / initial_eq) if initial_eq > 0 else 0.0
        drawdown_pct = round(drawdown_from_initial * 100.0, 3)

        self.current_session["session_pnl_usd"] = pnl_usd
        self.current_session["session_pnl_pct"] = pnl_pct
        self.current_session["current_drawdown_from_initial_pct"] = drawdown_pct
        self.current_session["max_observed_drawdown_pct"] = max(
            float(self.current_session.get("max_observed_drawdown_pct", 0.0)),
            drawdown_pct,
        )

        breached = drawdown_from_initial >= limit_pct
        if breached:
            self.current_session["circuit_breaker_triggered"] = True

        return {
            "breached_4pct": breached,
            "drawdown_pct": drawdown_pct,
            "limit_pct": round(limit_pct * 100.0, 2),
            "session_pnl_usd": pnl_usd,
            "current_equity": round(live_equity, 2),
            "hard_stop_equity": self.current_session["hard_stop_equity_4pct"],
        }

    # -----------------------------------------------------------------
    # REGISTRO DETALLADO DE OPERACIONES PARA AUDITORÍA
    # -----------------------------------------------------------------

    def record_trade_execution(
        self,
        opportunity: dict[str, Any],
        execution_result: dict[str, Any],
        execution_mode: str,
        order_type: str,
        portfolio_equity_at_entry: float,
    ) -> dict[str, Any]:
        """Registra una operación con todo su contexto técnico y matemático de riesgo."""
        trade_id = f"trd-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        session_id = (
            self.current_session["session_id"] if self.current_session else "manual-standalone"
        )

        broker_struct = execution_result.get("broker_order_structure", {})
        sizing = opportunity.get("order_sizing", {})
        metrics = opportunity.get("financial_metrics", {})

        entry_ref = float(opportunity.get("entry_price", broker_struct.get("reference_entry_price", 0.0)))
        limit_sent = broker_struct.get("limit_price") or entry_ref
        sl_price = float(sizing.get("stop_loss_price", broker_struct.get("stop_loss", {}).get("stop_price", 0.0)))
        tp_price = float(sizing.get("take_profit_price", broker_struct.get("take_profit", {}).get("limit_price", 0.0)))
        shares = int(sizing.get("shares_to_buy", broker_struct.get("qty", 0)))

        trade_record = {
            "trade_id": trade_id,
            "session_id": session_id,
            "alpaca_order_id": execution_result.get("order_id"),
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None,
            "execution_mode": execution_mode,
            "order_type": order_type,
            "broker_status": execution_result.get("status"),
            "ticker": str(opportunity.get("ticker", broker_struct.get("symbol", ""))).upper(),
            "market_regime": opportunity.get("market_regime", "UNKNOWN"),
            "technical_context_at_entry": {
                "entry_reference_price": entry_ref,
                "limit_price_submitted": limit_sent,
                "sma_200": opportunity.get("sma_200"),
                "price_above_sma200_pct": (
                    round(((entry_ref - float(opportunity["sma_200"])) / float(opportunity["sma_200"])) * 100, 2)
                    if opportunity.get("sma_200")
                    else None
                ),
                "adx_14": opportunity.get("adx"),
                "stoch_k": opportunity.get("stoch_k"),
                "stoch_d": opportunity.get("stoch_d"),
                "atr_14": opportunity.get("atr_14"),
                "atr_volatility_pct": (
                    round((float(opportunity["atr_14"]) / entry_ref) * 100, 2)
                    if opportunity.get("atr_14") and entry_ref > 0
                    else None
                ),
            },
            "risk_and_sizing": {
                "portfolio_equity_at_entry": round(portfolio_equity_at_entry, 2),
                "shares": shares,
                "capital_allocated": round(shares * entry_ref, 2),
                "stop_loss_price": sl_price,
                "take_profit_price": tp_price,
                "risk_per_share": round(entry_ref - sl_price, 2),
                "reward_per_share": round(tp_price - entry_ref, 2),
                "max_loss_with_commissions": metrics.get("max_loss_with_commissions"),
                "portfolio_risk_pct": metrics.get("portfolio_risk_pct"),
                "expected_net_profit": metrics.get("expected_net_profit"),
                "net_reward_risk_ratio": metrics.get("net_reward_risk_ratio"),
            },
            "lifecycle": {
                "state": "OPEN_OR_PENDING",
                "filled_avg_price": None,
                "actual_entry_slippage_usd": None,
                "exit_price": None,
                "exit_reason": None,
                "realized_pnl_usd": None,
                "realized_pnl_pct": None,
                "r_multiple_achieved": None,
            },
            "strategy_params_version": self.strategy_params.get("version", 1),
            "ai_advice": opportunity.get("ai_advice"),
        }

        self.trades_cache.insert(0, trade_record)
        self._rewrite_trades_ledger()

        if self.current_session:
            self.current_session["trades_opened"] = int(self.current_session.get("trades_opened", 0)) + 1

        logger.info(
            f"[AUDIT AGENT] Operación registrada: {trade_id} | {trade_record['ticker']} "
            f"({shares} acc @ ${entry_ref} | SL ${sl_price} | TP ${tp_price} | Modo={execution_mode})"
        )
        return trade_record

    def reconcile_with_alpaca_orders(self, alpaca_orders: list[dict[str, Any]]) -> int:
        """
        Cruza las órdenes registradas en el libro de auditoría con el estado real en Alpaca
        (incluyendo si saltó la pata de Take Profit o la pata de Stop Loss OCO) para calcular
        el PnL real, el múltiplo R y el motivo exacto de salida.
        """
        if not alpaca_orders or not self.trades_cache:
            return 0

        orders_by_id = {str(o.get("id")): o for o in alpaca_orders if o.get("id")}
        updated_count = 0

        for trade in self.trades_cache:
            order_id = str(trade.get("alpaca_order_id") or "")
            if not order_id or order_id not in orders_by_id:
                continue

            alp_ord = orders_by_id[order_id]
            lifecycle = trade["lifecycle"]
            ref_price = float(trade["technical_context_at_entry"]["entry_reference_price"] or 0.0)
            shares = int(trade["risk_and_sizing"]["shares"] or 0)
            risk_per_share = float(trade["risk_and_sizing"]["risk_per_share"] or 1.0)

            filled_entry = alp_ord.get("filled_avg_price")
            if filled_entry and lifecycle.get("filled_avg_price") is None:
                lifecycle["filled_avg_price"] = float(filled_entry)
                lifecycle["actual_entry_slippage_usd"] = round(float(filled_entry) - ref_price, 4)
                updated_count += 1

            # Revisar patas hijas OCO (Take Profit Limit vs Stop Loss Stop)
            for leg in alp_ord.get("legs", []):
                leg_status = str(leg.get("status", "")).lower()
                leg_type = str(leg.get("type", "")).lower()
                if "filled" in leg_status and lifecycle.get("state") != "CLOSED":
                    entry_px = float(lifecycle.get("filled_avg_price") or ref_price)
                    exit_px = float(
                        leg.get("filled_avg_price")
                        or leg.get("limit_price")
                        or leg.get("stop_price")
                        or entry_px
                    )
                    pnl_usd = round((exit_px - entry_px) * shares, 2)
                    pnl_pct = round(((exit_px - entry_px) / entry_px) * 100.0, 2) if entry_px > 0 else 0.0
                    r_mult = round((exit_px - entry_px) / risk_per_share, 2) if risk_per_share > 0 else 0.0

                    lifecycle["state"] = "CLOSED"
                    lifecycle["exit_price"] = exit_px
                    lifecycle["exit_reason"] = (
                        "TAKE_PROFIT_OCO_HIT" if "limit" in leg_type else "STOP_LOSS_OCO_HIT"
                    )
                    lifecycle["realized_pnl_usd"] = pnl_usd
                    lifecycle["realized_pnl_pct"] = pnl_pct
                    lifecycle["r_multiple_achieved"] = r_mult
                    trade["closed_at"] = datetime.now(timezone.utc).isoformat()
                    updated_count += 1

            if str(alp_ord.get("status", "")).lower() in ("canceled", "cancelled", "expired") and lifecycle.get("state") == "OPEN_OR_PENDING":
                lifecycle["state"] = "CANCELLED_UNFILLED"
                lifecycle["exit_reason"] = "ORDER_CANCELLED_BEFORE_FILL"
                trade["closed_at"] = datetime.now(timezone.utc).isoformat()
                updated_count += 1

        if updated_count > 0:
            self._rewrite_trades_ledger()
        return updated_count

    # -----------------------------------------------------------------
    # MOTOR DE AUDITORÍA CRÍTICA E ITERACIÓN DE PARÁMETROS
    # -----------------------------------------------------------------

    def finalize_session_and_audit(
        self,
        stop_reason: str,
        final_equity: float,
        market_watch_snapshot: list[dict[str, Any]],
        auto_apply_fixes: bool = False,
    ) -> dict[str, Any]:
        """
        Cierra la sesión actual, guarda el histórico y lanza la Auditoría Crítica Cuantitativa.
        """
        session_data = self.current_session
        if session_data:
            session_data["ended_at"] = datetime.now(timezone.utc).isoformat()
            session_data["stop_reason"] = stop_reason
            self.update_session_equity(final_equity, int(session_data.get("scans_executed", 0)))
            self.sessions_history.insert(0, dict(session_data))
            self._save_json_list(SESSIONS_FILE, self.sessions_history)

        report = self.generate_critical_audit_report(
            session_id=session_data["session_id"] if session_data else None,
            stop_reason=stop_reason,
            market_watch_snapshot=market_watch_snapshot,
            auto_apply_fixes=auto_apply_fixes,
        )
        self.current_session = None
        return report

    def generate_critical_audit_report(
        self,
        session_id: Optional[str] = None,
        stop_reason: str = "ON_DEMAND_AUDIT",
        market_watch_snapshot: Optional[list[dict[str, Any]]] = None,
        auto_apply_fixes: bool = False,
    ) -> dict[str, Any]:
        """
        Analiza críticamente todas las operaciones de la sesión (y el histórico reciente),
        identifica errores o debilidades cuantitativas y propone ajustes de parámetros.
        """
        relevant_trades = (
            [t for t in self.trades_cache if t.get("session_id") == session_id]
            if session_id
            else self.trades_cache[:50]
        )
        if not relevant_trades:
            relevant_trades = self.trades_cache[:30]

        closed_trades = [
            t for t in relevant_trades if t.get("lifecycle", {}).get("state") == "CLOSED"
        ]
        wins = [
            t for t in closed_trades if float(t.get("lifecycle", {}).get("realized_pnl_usd") or 0) > 0
        ]
        losses = [
            t for t in closed_trades if float(t.get("lifecycle", {}).get("realized_pnl_usd") or 0) < 0
        ]

        total_closed = len(closed_trades)
        win_rate_pct = round((len(wins) / total_closed) * 100.0, 2) if total_closed > 0 else 0.0
        gross_win = sum(float(t["lifecycle"]["realized_pnl_usd"]) for t in wins)
        gross_loss = abs(sum(float(t["lifecycle"]["realized_pnl_usd"]) for t in losses))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
        net_realized_pnl = round(gross_win - gross_loss, 2)

        # Desglose por régimen de mercado (BULLISH_TREND vs LATERAL_RANGE)
        lateral_trades = [t for t in closed_trades if "LATERAL" in str(t.get("market_regime", ""))]
        lateral_losses = [
            t for t in lateral_trades if float(t.get("lifecycle", {}).get("realized_pnl_usd") or 0) < 0
        ]
        bullish_trades = [t for t in closed_trades if "BULLISH" in str(t.get("market_regime", ""))]
        bullish_losses = [
            t for t in bullish_trades if float(t.get("lifecycle", {}).get("realized_pnl_usd") or 0) < 0
        ]

        # Análisis de Slippage real en entradas
        slippages = [
            float(t["lifecycle"]["actual_entry_slippage_usd"])
            for t in relevant_trades
            if t.get("lifecycle", {}).get("actual_entry_slippage_usd") is not None
        ]
        avg_slippage_usd = round(sum(slippages) / len(slippages), 4) if slippages else 0.0

        # -----------------------------------------------------------------
        # DIAGNÓSTICO CRÍTICO Y DETECCIÓN DE ERRORES OPERATIVOS
        # -----------------------------------------------------------------
        critical_findings: list[dict[str, Any]] = []
        recommended_adjustments: dict[str, Any] = {}

        # 1. Evaluación de disparo del cortafuegos del 4%
        if "CIRCUIT_BREAKER" in stop_reason or (
            self.current_session and self.current_session.get("circuit_breaker_triggered")
        ):
            critical_findings.append(
                {
                    "severity": "CRITICAL",
                    "code": "DRAWDOWN_4PCT_BREACHED",
                    "title": "Parada de Emergencia por Pérdida del 4% de la Cartera",
                    "analysis": (
                        "La sesión alcanzó el umbral máximo de pérdida permitida (4% del equity). "
                        "Con un riesgo teórico del 1% por operación, alcanzar el -4% implica una racha de "
                        "4 Stop Loss consecutivos o pérdidas latentes simultáneas en posiciones correlacionadas."
                    ),
                    "recommendation": (
                        "Endurecer el umbral de sobreventa del Estocástico y ampliar ligeramente el multiplicador "
                        "ATR del Stop Loss para evitar barridos por ruido intradía."
                    ),
                }
            )
            curr_stoch_bull = float(self.strategy_params.get("stoch_bullish_oversold", 25.0))
            if curr_stoch_bull > 18.0:
                recommended_adjustments["stoch_bullish_oversold"] = max(15.0, round(curr_stoch_bull - 3.0, 1))

        # 2. Evaluación de barridos de Stop Loss (Stop Loss demasiado ceñido)
        if total_closed >= 2 and len(losses) / total_closed > 0.60:
            curr_atr_mult = float(self.strategy_params.get("atr_stop_loss_multiplier", 1.5))
            new_atr_mult = round(min(2.2, curr_atr_mult + 0.2), 2)
            critical_findings.append(
                {
                    "severity": "HIGH",
                    "code": "HIGH_STOP_LOSS_SWEEP_RATE",
                    "title": f"Tasa de Stop Loss elevada ({len(losses)}/{total_closed} operaciones cerradas en pérdida)",
                    "analysis": (
                        f"El Win Rate actual ({win_rate_pct}%) es inferior al mínimo necesario (~36%) para "
                        f"rentabilizar un ratio Riesgo/Beneficio de 1:2. El Stop Loss a {curr_atr_mult}x ATR "
                        "está siendo alcanzado por la volatilidad normal antes de que el rebote madure."
                    ),
                    "recommendation": f"Incrementar `atr_stop_loss_multiplier` de {curr_atr_mult} a {new_atr_mult}.",
                }
            )
            recommended_adjustments["atr_stop_loss_multiplier"] = new_atr_mult

        # 3. Evaluación crítica del Régimen Lateral vs Tendencial
        if len(lateral_trades) >= 2 and (len(lateral_losses) / len(lateral_trades)) >= 0.65:
            curr_lat_stoch = float(self.strategy_params.get("stoch_lateral_oversold", 20.0))
            new_lat_stoch = max(12.0, round(curr_lat_stoch - 3.0, 1))
            critical_findings.append(
                {
                    "severity": "MEDIUM",
                    "code": "LATERAL_REGIME_UNDERPERFORMANCE",
                    "title": "Falsas señales en Régimen Lateral (ADX < 20)",
                    "analysis": (
                        f"Las entradas en rango lateral presentan un {round((len(lateral_losses)/len(lateral_trades))*100, 1)}% "
                        "de fallos. Cuando el activo carece de tendencia alcista (Precio < SMA200), los cruces "
                        "del estocástico en nivel 20 pueden ser trampas bajistas."
                    ),
                    "recommendation": (
                        f"Exigir sobreventa más extrema en lateral (`stoch_lateral_oversold`: {curr_lat_stoch} -> {new_lat_stoch})."
                    ),
                }
            )
            recommended_adjustments["stoch_lateral_oversold"] = new_lat_stoch

        # 4. Análisis del contexto de mercado en vivo si no hubo señales disparadas
        if len(relevant_trades) == 0 and market_watch_snapshot:
            near_triggers = [
                m for m in market_watch_snapshot
                if float(m.get("stoch_k", 100)) <= 30.0
            ]
            critical_findings.append(
                {
                    "severity": "INFO",
                    "code": "ZERO_TRADES_FILTER_DIAGNOSIS",
                    "title": "Sesión sin operaciones ejecutadas — Diagnóstico de Filtros",
                    "analysis": (
                        f"Se vigilaron {len(market_watch_snapshot)} activos sin que coincidiera simultáneamente "
                        "el régimen (Tendencial/Lateral) con el cruce exacto de %K sobre %D en zona de sobreventa. "
                        f"Activos actualmente en zona baja (%K <= 30): {[m['ticker'] for m in near_triggers]}."
                    ),
                    "recommendation": (
                        "Comportamiento correcto bajo disciplina estricta: muchos activos están en sobreventa "
                        "pero %K aún no ha cruzado al alza sobre %D (evitando atrapar cuchillos cayendo)."
                    ),
                }
            )

        if not critical_findings:
            critical_findings.append(
                {
                    "severity": "OK",
                    "code": "OPERATIONS_WITHIN_PARAMETERS",
                    "title": "Operativa dentro de los parámetros cuantitativos esperados",
                    "analysis": (
                        f"Se registraron {len(relevant_trades)} operaciones ({total_closed} cerradas, "
                        f"Win Rate {win_rate_pct}%, Profit Factor {profit_factor}). Sin desviaciones críticas."
                    ),
                    "recommendation": "Mantener parámetros actuales y continuar recopilando muestra estadística.",
                }
            )

        applied_Params = None
        if auto_apply_fixes and recommended_adjustments:
            applied_Params = self.save_strategy_params(
                recommended_adjustments, updated_by="AUTO_AUDIT_CORRECTION"
            )

        audit_id = f"aud-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        report = {
            "audit_id": audit_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "trigger_reason": stop_reason,
            "session_summary": self.current_session,
            "quantitative_metrics": {
                "total_trades_evaluated": len(relevant_trades),
                "closed_trades": total_closed,
                "open_or_pending_trades": len(relevant_trades) - total_closed,
                "wins": len(wins),
                "losses": len(losses),
                "win_rate_pct": win_rate_pct,
                "profit_factor": profit_factor,
                "net_realized_pnl_usd": net_realized_pnl,
                "avg_entry_slippage_usd": avg_slippage_usd,
                "bullish_trend_trades": len(bullish_trades),
                "bullish_trend_losses": len(bullish_losses),
                "lateral_range_trades": len(lateral_trades),
                "lateral_range_losses": len(lateral_losses),
            },
            "critical_findings": critical_findings,
            "recommended_parameter_adjustments": recommended_adjustments,
            "auto_applied": bool(applied_Params is not None),
            "current_strategy_params": self.strategy_params,
        }

        self.audit_reports.insert(0, report)
        self._save_json_list(REPORTS_FILE, self.audit_reports)
        logger.info(
            f"[AUDIT AGENT] Informe de Auditoría Crítica generado: {audit_id} | "
            f"Hallazgos={len(critical_findings)} | Ajustes propuestos={recommended_adjustments}"
        )
        return report

    def apply_audit_recommendations(self, audit_id: str) -> dict[str, Any]:
        for rep in self.audit_reports:
            if rep.get("audit_id") == audit_id:
                adjustments = rep.get("recommended_parameter_adjustments") or {}
                if not adjustments:
                    return {
                        "status": "NO_ADJUSTMENTS_NEEDED",
                        "message": "Este informe de auditoría no requería cambios de parámetros.",
                        "strategy_params": self.strategy_params,
                    }
                updated = self.save_strategy_params(adjustments, updated_by=f"AUDIT_{audit_id}")
                rep["auto_applied"] = True
                self._save_json_list(REPORTS_FILE, self.audit_reports)
                return {
                    "status": "PARAMETERS_UPDATED",
                    "applied_adjustments": adjustments,
                    "strategy_params": updated,
                }
        raise ValueError(f"No se encontró el informe de auditoría {audit_id}")


audit_agent = IndependentAuditAgent()
