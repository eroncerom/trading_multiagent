import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("quant-audit-agent")

AUDIT_DIR = Path("/app/audit_data") if Path("/app").exists() else Path(__file__).resolve().parent / "audit_data"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

TRADES_LEDGER_FILE = AUDIT_DIR / "trades_ledger.jsonl"
SESSIONS_FILE = AUDIT_DIR / "sessions_history.json"
REPORTS_FILE = AUDIT_DIR / "audit_reports.json"
STRATEGY_PARAMS_FILE = AUDIT_DIR / "strategy_params.json"
POSITION_EXCURSIONS_FILE = AUDIT_DIR / "position_excursions.json"
DYNAMIC_ADJUSTMENTS_LOG_FILE = AUDIT_DIR / "dynamic_adjustments_ledger.json"

CLUSTER_SECTOR_BENCHMARKS: dict[str, dict[str, str]] = {
    "TECH_GROWTH_A": {"sector_etf": "SOXX", "secondary_etf": "QQQ", "sector_name": "Semiconductores & Mega-Tech"},
    "TECH_GROWTH_B": {"sector_etf": "XLK", "secondary_etf": "SOXX", "sector_name": "Software & Semiconductores"},
    "FINANCE_CONSUMER_A": {"sector_etf": "XLF", "secondary_etf": "SPY", "sector_name": "Banca & Consumo Global"},
    "FINANCE_CONSUMER_B": {"sector_etf": "XLF", "secondary_etf": "SPY", "sector_name": "Finanzas & Retail"},
    "HEALTH_INDUSTRIAL_A": {"sector_etf": "XLV", "secondary_etf": "XLI", "sector_name": "Salud, Defensa & Energía"},
    "HEALTH_INDUSTRIAL_B": {"sector_etf": "XLI", "secondary_etf": "XLV", "sector_name": "Biotecnología & Industrial"},
    "HIGH_BETA_EXPANSION_A": {"sector_etf": "QQQ", "secondary_etf": "IWM", "sector_name": "High-Beta Cloud & Crypto/Fintech"},
    "HIGH_BETA_EXPANSION_B": {"sector_etf": "IWM", "secondary_etf": "QQQ", "sector_name": "Crecimiento Disruptivo High-Beta"},
    "CYBER_CLOUD_ENTERPRISE": {"sector_etf": "IGV", "secondary_etf": "XLK", "sector_name": "Ciberseguridad & Enterprise SaaS"},
    "SEMI_AI_INFRASTRUCTURE": {"sector_etf": "SMH", "secondary_etf": "SOXX", "sector_name": "Infraestructura IA & Redes"},
    "FINTECH_INSURANCE_REIT": {"sector_etf": "XLF", "secondary_etf": "XLRE", "sector_name": "Fintech, Seguros & REITs"},
    "CONSUMER_TRAVEL_AUTO": {"sector_etf": "XLY", "secondary_etf": "SPY", "sector_name": "Viajes, Ocio & Automoción"},
    "BIOTECH_MED_DIAGNOSTICS": {"sector_etf": "XBI", "secondary_etf": "XLV", "sector_name": "Biotecnología & Diagnóstico"},
    "DEFENSE_AERO_TRANSPORT": {"sector_etf": "ITA", "secondary_etf": "XLI", "sector_name": "Aeroespacial, Defensa & Logística"},
    "ENERGY_MINING_MATERIALS": {"sector_etf": "XLE", "secondary_etf": "XLB", "sector_name": "Energía, Uranio, Oro & Materiales"},
    "UTILITIES_GRID_DIVIDEND": {"sector_etf": "XLU", "secondary_etf": "XLP", "sector_name": "Redes Eléctricas IA & Dividendos"},
    "IPO_INCUBATOR": {"sector_etf": "IPO", "secondary_etf": "QQQ", "sector_name": "Incubadora IPOs & Debuts"},
}


DEFAULT_CLUSTERS: dict[str, list[str]] = {
    "TECH_GROWTH_A": [
        "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL",
        "META", "TSLA", "NFLX", "PLTR", "AMD",
        "AVGO", "CRM", "UBER", "INTC", "CSCO",
    ],
    "TECH_GROWTH_B": [
        "ORCL", "ADBE", "QCOM", "TXN", "AMAT",
        "INTU", "NOW", "IBM", "MU", "LRCX",
        "ADI", "KLAC", "SNPS", "CDNS", "MRVL",
    ],
    "FINANCE_CONSUMER_A": [
        "JPM", "BAC", "WFC", "GS", "MS",
        "V", "MA", "PYPL", "KO", "PEP",
        "COST", "WMT", "NKE", "MCD", "DIS",
    ],
    "FINANCE_CONSUMER_B": [
        "AXP", "BLK", "C", "SCHW", "SPGI",
        "PGR", "CB", "HD", "LOW", "TGT",
        "SBUX", "TJX", "BKNG", "PM", "PG",
    ],
    "HEALTH_INDUSTRIAL_A": [
        "JNJ", "PFE", "ABBV", "MRK", "LLY",
        "UNH", "CAT", "DE", "GE", "HON",
        "BA", "LMT", "XOM", "CVX", "UPS",
    ],
    "HEALTH_INDUSTRIAL_B": [
        "TMO", "ABT", "DHR", "ISRG", "AMGN",
        "GILD", "VRTX", "BMY", "RTX", "ETN",
        "UNP", "ADP", "COP", "SLB", "EOG",
    ],
    "HIGH_BETA_EXPANSION_A": [
        "SOFI", "COIN", "HOOD", "SHOP", "SNOW",
        "CRWD", "PANW", "NET", "XYZ", "AFRM",
        "SMCI", "ARM", "RIVN", "DKNG", "MARA",
    ],
    "HIGH_BETA_EXPANSION_B": [
        "MELI", "SE", "DASH", "RBLX", "U",
        "ROKU", "TTD", "APP", "DDOG", "ZS",
        "MDB", "MSTR", "RIOT", "IONQ", "RKLB",
    ],
    "CYBER_CLOUD_ENTERPRISE": [
        "FTNT", "OKTA", "TEAM", "WDAY", "HUBS",
        "DOCU", "TWLO", "ZM", "DOCN", "FROG",
        "IOT", "GTLB", "ESTC", "NTNX", "VEEV",
    ],
    "SEMI_AI_INFRASTRUCTURE": [
        "ASML", "TSM", "ON", "NXPI", "MPWR",
        "SWKS", "QRVO", "TER", "ENTG", "ONTO",
        "ANET", "DELL", "HPE", "VRT", "APH",
    ],
    "FINTECH_INSURANCE_REIT": [
        "BRK-B", "CME", "ICE", "MCO", "AON",
        "MRSH", "MET", "AFL", "ALL", "TRV",
        "PLD", "AMT", "EQIX", "O", "SPG",
    ],
    "CONSUMER_TRAVEL_AUTO": [
        "ABNB", "MAR", "HLT", "EXPE", "RCL",
        "CCL", "DAL", "UAL", "GM", "F",
        "CMG", "YUM", "DPZ", "LULU", "ORLY",
    ],
    "BIOTECH_MED_DIAGNOSTICS": [
        "REGN", "BIIB", "MRNA", "ALNY", "INCY",
        "PODD", "IDXX", "DXCM", "ZBH", "BSX",
        "SYK", "MDT", "BDX", "EW", "IQV",
    ],
    "DEFENSE_AERO_TRANSPORT": [
        "NOC", "GD", "LHX", "HWM", "TDG",
        "HEI", "TXT", "AXON", "FDX", "CSX",
        "NSC", "ODFL", "URI", "PWR", "EMR",
    ],
    "ENERGY_MINING_MATERIALS": [
        "OXY", "MPC", "PSX", "VLO", "HAL",
        "BKR", "KMI", "WMB", "CCJ", "NEM",
        "FCX", "GOLD", "LIN", "APD", "SHW",
    ],
    "UTILITIES_GRID_DIVIDEND": [
        "NEE", "CEG", "VST", "DUK", "SO",
        "AEP", "D", "EXC", "CL", "KMB",
        "MDLZ", "GIS", "HSY", "KHC", "KR",
    ],
    "IPO_INCUBATOR": [
        "RDDT", "ARM", "KVUE", "BIRK", "CART",
        "ALAB", "ANRO", "TEM",
    ],
}

DEFAULT_STRATEGY_PARAMS: dict[str, Any] = {
    "version": 50,
    "engine_mode": "THREE_TRICKS_INTRADAY",
    "stoch_k_window": 14,
    "stoch_d_smooth": 4,
    "adx_window": 14,
    "ema_fast_window": 20,
    "sma_mid_window": 50,
    "sma_window": 200,
    "rsi_window": 14,
    "atr_window": 14,
    "min_rvol_intraday": 1.25,
    "max_dist_from_ema20_atr": 1.20,
    "max_rsi_15m_entry": 70.0,
    "min_clv_ignition": 0.65,
    "max_upper_wick_ratio": 0.25,
    "pullback_max_dist_atr": 0.60,
    "min_volume_ratio_impulse_vs_pb": 1.0,
    "min_quant_score": 70,
    "intraday_opening_cooldown_minutes": 15,
    "atr_stop_loss_multiplier": 1.25,
    "intraday_sl_buffer_atr": 0.25,
    "intraday_sl_min_atr": 0.80,
    "intraday_sl_max_atr": 1.50,
    "intraday_sl_min_price_pct": 0.003,
    "intraday_sl_max_price_pct": 0.018,
    "take_profit_reward_risk_ratio": 1.90,
    "min_reward_risk_ratio": 1.60,
    "max_single_swing_gain_pct": 0.04,
    "cap_tp_at_swing_high_in_uptrend": True,
    "max_portfolio_risk_per_trade_pct": 0.0028,
    "use_adaptive_allocation_cap": True,
    "max_position_allocation_pct": 0.30,
    "virtual_portfolio_base": 5000.0,
    "commission_per_order": 1.0,
    "max_comm_ratio": 0.25,
    "min_net_profit_usd": 8.0,
    "max_session_drawdown_pct": 0.04,
    "enable_breakeven_protection": True,
    "breakeven_trigger_usd": 8.0,
    "breakeven_trigger_r": 0.60,
    "breakeven_lock_r": 0.15,
    "breakeven_net_cushion_usd": 2.0,
    "trailing_stage1_trigger_r": 1.00,
    "trailing_stage1_trigger_usd": 12.0,
    "trailing_stage1_lock_mfe_pct": 0.60,
    "trailing_stage2_trigger_r": 1.40,
    "trailing_stage2_trigger_usd": 18.0,
    "trailing_stage2_lock_mfe_pct": 0.75,
    "trailing_ratchet_trigger_r": 1.00,
    "trailing_ratchet_lock_r": 0.60,
    "early_abort_selling_rvol": 1.30,
    "exhaustion_close_min_r": 1.00,
    "swing_overnight_min_r": 1.50,
    "swing_overnight_min_clv": 0.70,
    "eod_close_window_minutes": 15,
    "ai_gate_full_auto": True,
    "ai_veto_on_caution": True,
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
    "last_updated_by": "SYSTEM_DEFAULT_V50",
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
        self.position_excursions: dict[str, dict[str, Any]] = self._load_json_dict(POSITION_EXCURSIONS_FILE)
        self._reconcile_lock = threading.Lock()
        self.dynamic_adjustments_log: list[dict[str, Any]] = self._load_json_list(DYNAMIC_ADJUSTMENTS_LOG_FILE)
        self.auto_adapt_positions: bool = bool(self.strategy_params.get("auto_adapt_positions", False))
        self._telemetry_cache: dict[str, Any] = {"timestamp": 0.0, "data": {}}
        self.last_cluster_audit: Optional[dict[str, Any]] = None
        self.last_active_positions_audit: Optional[dict[str, Any]] = None

    def _load_json_dict(self, path: Path) -> dict[str, Any]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                logger.warning(f"Error cargando {path.name}: {exc}")
        return {}

    def _save_json_dict(self, path: Path, data: dict[str, Any]) -> None:
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error(f"Error escribiendo {path.name}: {exc}")

    def _load_strategy_params(self) -> dict[str, Any]:
        if STRATEGY_PARAMS_FILE.exists():
            try:
                data = json.loads(STRATEGY_PARAMS_FILE.read_text(encoding="utf-8"))
                merged = {**DEFAULT_STRATEGY_PARAMS, **data}
                existing_clusters = merged.get("clusters", {})
                needs_save = False
                if len(existing_clusters) < 16 or "CYBER_CLOUD_ENTERPRISE" not in existing_clusters:
                    logger.info("Migrando clusters a la arquitectura de 16 subagentes (240 activos)...")
                    merged["clusters"] = DEFAULT_CLUSTERS
                    merged["version"] = max(int(merged.get("version", 1)), 9)
                    needs_save = True
                
                # Incorporación de IPO_INCUBATOR (Clúster #17 de 15 plazas)
                if "IPO_INCUBATOR" not in merged.get("clusters", {}):
                    logger.info("Añadiendo clúster Incubadora IPO Sandbox (IPO_INCUBATOR)...")
                    clusters = dict(merged.get("clusters", {}))
                    clusters["IPO_INCUBATOR"] = list(DEFAULT_CLUSTERS["IPO_INCUBATOR"])
                    merged["clusters"] = clusters
                    merged["version"] = max(int(merged.get("version", 1)), 10)
                    needs_save = True

                if needs_save:
                    STRATEGY_PARAMS_FILE.write_text(
                        json.dumps(merged, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                return merged
            except Exception as exc:
                logger.warning(f"Error cargando strategy_params.json: {exc}")
        return dict(DEFAULT_STRATEGY_PARAMS)

    def add_ticker_to_incubator(self, ticker: str, replace_ticker: Optional[str] = None) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        clusters = dict(self.strategy_params.get("clusters", {}))
        incubator = list(clusters.get("IPO_INCUBATOR", []))

        if ticker in incubator:
            return {
                "success": False,
                "message": f"El ticker {ticker} ya está en la Incubadora de IPOs.",
                "tickers": incubator,
                "used_slots": len(incubator),
                "total_slots": 15,
            }

        if len(incubator) >= 15:
            if not replace_ticker or replace_ticker.upper() not in incubator:
                return {
                    "success": False,
                    "requires_replacement": True,
                    "message": "La Incubadora está completa (15/15 plazas ocupadas). Selecciona a cuál relevar.",
                    "tickers": incubator,
                    "used_slots": len(incubator),
                    "total_slots": 15,
                }
            # Reemplazar ticker indicado
            idx = incubator.index(replace_ticker.upper())
            relever = incubator[idx]
            incubator[idx] = ticker
            msg = f"{ticker} añadido a la Incubadora relevando a {relever} (15/15 plazas)."
        else:
            if replace_ticker and replace_ticker.upper() in incubator:
                idx = incubator.index(replace_ticker.upper())
                incubator[idx] = ticker
                msg = f"{ticker} añadido relevando a {replace_ticker} ({len(incubator)}/15 plazas)."
            else:
                incubator.append(ticker)
                msg = f"{ticker} añadido a la Incubadora con éxito ({len(incubator)}/15 plazas)."

        clusters["IPO_INCUBATOR"] = incubator
        self.strategy_params["clusters"] = clusters
        self.save_strategy_params({"clusters": clusters}, updated_by="IPO_INCUBATOR_ADD")
        return {
            "success": True,
            "message": msg,
            "ticker": ticker,
            "used_slots": len(incubator),
            "total_slots": 15,
            "tickers": incubator,
        }

    def remove_ticker_from_incubator(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        clusters = dict(self.strategy_params.get("clusters", {}))
        incubator = list(clusters.get("IPO_INCUBATOR", []))

        if ticker not in incubator:
            return {
                "success": False,
                "message": f"El ticker {ticker} no está en la Incubadora.",
                "tickers": incubator,
                "used_slots": len(incubator),
                "total_slots": 15,
            }

        incubator.remove(ticker)
        clusters["IPO_INCUBATOR"] = incubator
        self.strategy_params["clusters"] = clusters
        self.save_strategy_params({"clusters": clusters}, updated_by="IPO_INCUBATOR_REMOVE")
        return {
            "success": True,
            "message": f"{ticker} dado de baja de la Incubadora. Plaza liberada ({len(incubator)}/15).",
            "ticker": ticker,
            "used_slots": len(incubator),
            "total_slots": 15,
            "tickers": incubator,
        }

    def graduate_ticker_from_incubator(
        self, ticker: str, target_cluster: str, replace_ticker: str
    ) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        replace_ticker = replace_ticker.strip().upper()
        clusters = dict(self.strategy_params.get("clusters", {}))
        incubator = list(clusters.get("IPO_INCUBATOR", []))

        if ticker not in incubator:
            return {"success": False, "message": f"{ticker} no está en la Incubadora."}
        if target_cluster not in clusters:
            return {"success": False, "message": f"El clúster destino {target_cluster} no existe."}

        target_list = list(clusters[target_cluster])
        if replace_ticker not in target_list:
            return {
                "success": False,
                "message": f"El activo a relevar {replace_ticker} no existe en {target_cluster}.",
            }

        incubator.remove(ticker)
        idx = target_list.index(replace_ticker)
        target_list[idx] = ticker

        clusters["IPO_INCUBATOR"] = incubator
        clusters[target_cluster] = target_list
        self.strategy_params["clusters"] = clusters
        self.save_strategy_params({"clusters": clusters}, updated_by="IPO_INCUBATOR_GRADUATE")
        return {
            "success": True,
            "message": f"{ticker} graduado exitosamente a {target_cluster} en sustitución de {replace_ticker}.",
            "graduated_ticker": ticker,
            "target_cluster": target_cluster,
            "replaced_ticker": replace_ticker,
            "incubator_tickers": incubator,
            "target_cluster_tickers": target_list,
        }

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

    _EXIT_REASON_LABELS = {
        "TAKE_PROFIT_OCO_HIT": "Target alcanzado (Take Profit)",
        "TAKE_PROFIT_LIMIT_FILLED": "Target alcanzado (Take Profit)",
        "STOP_LOSS_OCO_HIT": "Stop Loss activado",
        "STOP_LOSS_STOP_FILLED": "Stop Loss activado",
        "STOP_LOSS_SHIELD_FILLED": "Stop Loss activado",
        "EARLY_ABORT_BY_REVIEWER": "Cierre preventivo por Revisor de Entradas",
        "TAKE_PROFIT_EXHAUSTION_CLOSE": "Toma de beneficios por agotamiento intradía",
        "EOD_CLOSE_POSITION": "Cierre fin de sesión intradía (EOD)",
        "PROFIT_LOCK_MARKET_CLOSE": "Protección de beneficios a mercado (Profit Lock)",
    }

    def _notify_position_closed(self, trade: dict[str, Any]) -> bool:
        """Envía UNA sola notificación de venta (resultado en $ y %) al webhook de n8n -> Telegram."""
        lc = trade.get("lifecycle", {})
        if lc.get("exit_notified") is not False or lc.get("realized_pnl_usd") is None:
            return False
        url = os.getenv("N8N_OPPORTUNITY_WEBHOOK_URL", "").strip()
        if not url:
            lc["exit_notified"] = True
            return True
        pnl_usd = float(lc["realized_pnl_usd"])
        pnl_pct = float(lc.get("realized_pnl_pct") or 0.0)
        sign = "+" if pnl_usd >= 0 else "-"
        reason = str(lc.get("exit_reason") or "")
        payload = {
            "event": "POSITION_CLOSED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": [
                {
                    "ticker": trade.get("ticker"),
                    "shares": trade.get("risk_and_sizing", {}).get("shares"),
                    "entry_price": lc.get("filled_avg_price")
                    or trade.get("technical_context_at_entry", {}).get("entry_reference_price"),
                    "exit_price": lc.get("exit_price"),
                    "realized_pnl_usd": round(pnl_usd, 2),
                    "realized_pnl_pct": round(pnl_pct, 2),
                    "result_emoji": "🟢" if pnl_usd >= 0 else "🔴",
                    "result_text": f"{sign}${abs(pnl_usd):.2f} ({sign}{abs(pnl_pct):.2f}%)",
                    "exit_reason_label": self._EXIT_REASON_LABELS.get(reason, reason or "Cierre de posición"),
                }
            ],
        }
        try:
            httpx.post(url, json=payload, timeout=5.0)
            lc["exit_notified"] = True
            return True
        except Exception as exc:
            logger.warning(f"No se pudo notificar la venta de {trade.get('ticker')}: {exc}")
            return False  # exit_notified sigue en False: se reintenta en la próxima conciliación

    def reconcile_with_alpaca_orders(self, alpaca_orders: list[dict[str, Any]]) -> int:
        with self._reconcile_lock:
            return self._reconcile_impl(alpaca_orders)

    def _reconcile_impl(self, alpaca_orders: list[dict[str, Any]]) -> int:
        """
        Cruza las órdenes registradas en el libro de auditoría con el estado real en Alpaca
        (incluyendo si saltó la pata de Take Profit o la pata de Stop Loss OCO) para calcular
        el PnL real, el múltiplo R y el motivo exacto de salida.
        """
        if not alpaca_orders or not self.trades_cache:
            return 0

        orders_by_id = {str(o.get("id")): o for o in alpaca_orders if o.get("id")}
        filled_sells = [
            o for o in alpaca_orders
            if str(o.get("side", "")).lower() in ("sell", "orderside.sell")
            and str(o.get("status", "")).lower() in ("filled", "orderstatus.filled")
            and float(o.get("filled_qty") or 0) > 0
        ]
        used_sell_order_ids = set()
        updated_count = 0

        for trade in self.trades_cache:
            order_id = str(trade.get("alpaca_order_id") or "")
            alp_ord = orders_by_id.get(order_id)
            lifecycle = trade["lifecycle"]
            ref_price = float(trade["technical_context_at_entry"]["entry_reference_price"] or 0.0)
            shares = int(trade["risk_and_sizing"]["shares"] or 0)
            risk_per_share = float(trade["risk_and_sizing"]["risk_per_share"] or 1.0)

            if alp_ord:
                filled_entry = alp_ord.get("filled_avg_price")
                if filled_entry and lifecycle.get("filled_avg_price") is None:
                    lifecycle["filled_avg_price"] = float(filled_entry)
                    lifecycle["actual_entry_slippage_usd"] = round(float(filled_entry) - ref_price, 4)
                    updated_count += 1

                # Revisar patas hijas OCO (Take Profit Limit vs Stop Loss Stop)
                for leg in alp_ord.get("legs", []) or []:
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
                        lifecycle["exit_notified"] = False
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

            # Si las legs fueron reemplazadas/modificadas o la orden de compra original ya no está en el snapshot reciente
            if (
                lifecycle.get("state") == "OPEN_OR_PENDING"
                and lifecycle.get("filled_avg_price") is not None
            ):
                entry_px = float(lifecycle["filled_avg_price"])
                for sell_ord in filled_sells:
                    sell_id = str(sell_ord.get("id"))
                    if sell_id in used_sell_order_ids:
                        continue
                    if str(sell_ord.get("symbol")) == str(trade.get("ticker")):
                        sell_qty = float(sell_ord.get("filled_qty") or 0)
                        if sell_qty >= shares:
                            exit_px = float(sell_ord.get("filled_avg_price") or entry_px)
                            pnl_usd = round((exit_px - entry_px) * shares, 2)
                            pnl_pct = round(((exit_px - entry_px) / entry_px) * 100.0, 2) if entry_px > 0 else 0.0
                            r_mult = round((exit_px - entry_px) / risk_per_share, 2) if risk_per_share > 0 else 0.0

                            lifecycle["state"] = "CLOSED"
                            lifecycle["exit_notified"] = False
                            lifecycle["exit_price"] = exit_px
                            lifecycle["exit_reason"] = (
                                lifecycle.get("pending_exit_reason")
                                or ("TAKE_PROFIT_LIMIT_FILLED" if exit_px >= entry_px else "STOP_LOSS_STOP_FILLED")
                            )
                            lifecycle["realized_pnl_usd"] = pnl_usd
                            lifecycle["realized_pnl_pct"] = pnl_pct
                            lifecycle["r_multiple_achieved"] = r_mult
                            trade["closed_at"] = str(sell_ord.get("filled_at") or sell_ord.get("created_at") or datetime.now(timezone.utc).isoformat())
                            used_sell_order_ids.add(sell_id)
                            updated_count += 1
                            break

        for trade in self.trades_cache:
            if self._notify_position_closed(trade):
                updated_count += 1

        if updated_count > 0:
            self._apply_breakeven_plus_to_active_runners()
            self._rewrite_trades_ledger()
        return updated_count

    def _apply_breakeven_plus_to_active_runners(self) -> None:
        """Si un tramo ha cerrado con beneficio (TP1), busca tramos hermanos activos y blinda su SL a Breakeven+."""
        for trade in self.trades_cache:
            if trade.get("lifecycle", {}).get("state") == "OPEN_OR_PENDING":
                ticker = trade.get("ticker")
                has_tp_closed = any(
                    t.get("ticker") == ticker
                    and t.get("lifecycle", {}).get("state") == "CLOSED"
                    and float(t.get("lifecycle", {}).get("realized_pnl_usd") or 0) > 0
                    for t in self.trades_cache
                )
                if has_tp_closed:
                    ref_entry = float(trade["technical_context_at_entry"].get("entry_reference_price") or trade["lifecycle"].get("filled_avg_price") or 0.0)
                    risk_per_share = float(trade["risk_and_sizing"].get("risk_per_share") or 1.0)
                    offset_r = float(self.strategy_params.get("scale_out_breakeven_offset_r", 0.10))
                    be_plus = round(ref_entry + (offset_r * risk_per_share), 2)
                    curr_sl = float(trade["risk_and_sizing"].get("stop_loss_price") or 0.0)
                    if be_plus > curr_sl:
                        trade["risk_and_sizing"]["stop_loss_price"] = be_plus
                        trade["risk_and_sizing"]["breakeven_shield_active"] = True

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

        # 4. Integrar hallazgos en vivo de los Subagentes #11 (Clústeres) y #12 (Posiciones Activas)
        if self.last_active_positions_audit:
            for pos_diag in self.last_active_positions_audit.get("positions_diagnostics", []):
                if pos_diag.get("action_required"):
                    sev = "CRITICAL" if pos_diag.get("urgency") == "HIGH" else "WARNING"
                    critical_findings.append(
                        {
                            "severity": sev,
                            "code": f"DYNAMIC_POSITION_{pos_diag.get('regime_code', 'ALERT')}",
                            "title": f"[{pos_diag['symbol']}] {pos_diag['headline']}",
                            "analysis": pos_diag["proposal_explanation"]["why_proposed"],
                            "recommendation": pos_diag["proposal_explanation"]["action_summary"],
                        }
                    )

        if market_watch_snapshot:
            cl_audit = self.audit_clusters_and_signals(
                market_watch=market_watch_snapshot,
                active_opportunities=[],
                active_positions=[],
            )
            near_list = [n["ticker"] for n in cl_audit.get("near_triggers_radar", [])[:8]]
            anomalies_cnt = cl_audit.get("math_integrity", {}).get("anomalies_detected", 0)
            critical_findings.append(
                {
                    "severity": "WARNING" if anomalies_cnt > 0 else "INFO",
                    "code": "SUBAGENT_11_CLUSTER_AUDIT",
                    "title": f"Subagente #11: Auditados {cl_audit.get('total_assets_audited', 0)} activos en {cl_audit.get('clusters_audited', 8)} clústeres ({anomalies_cnt} anomalías)",
                    "analysis": (
                        f"Integridad matemática al {cl_audit.get('math_integrity', {}).get('integrity_score_pct', 100)}%. "
                        f"Se detectaron {len(cl_audit.get('near_triggers_radar', []))} activos en radar de pre-activación (%K <= 32): {near_list}."
                    ),
                    "recommendation": (
                        "Subagentes operando con cálculos verificados. Vigilar activos del radar de pre-cruce."
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

    # =====================================================================
    # SUBAGENTE #10: AUDITOR CUANTITATIVO DE CLÚSTERES Y CÁLCULOS PRE-TRADE
    # =====================================================================

    def _resolve_ticker_cluster(self, ticker: str) -> str:
        t_up = ticker.strip().upper()
        clusters = self.strategy_params.get("clusters") or DEFAULT_CLUSTERS
        for c_id, t_list in clusters.items():
            if t_up in [x.strip().upper() for x in t_list]:
                return c_id
        return "TECH_GROWTH_A"

    def audit_clusters_and_signals(
        self,
        market_watch: list[dict[str, Any]],
        active_opportunities: list[dict[str, Any]],
        active_positions: list[dict[str, Any]],
        portfolio_value: float = 5000.0,
    ) -> dict[str, Any]:
        """
        Subagente #10 (ClusterQuantitativeAuditor):
        Audita en memoria (< 5 ms) los cálculos entregados por los 8 subagentes de clúster:
        1. Integridad matemática de precios, ATR, SMA200, osciladores y niveles SL/TP.
        2. Salud y amplitud por cada uno de los 8 clústeres.
        3. Radar de Near-Triggers (%K <= 32 convergiendo hacia cruce alcista sobre %D).
        4. Veto/Alerta de Correlación Cruzada entre oportunidades y posiciones abiertas en cartera.
        """
        t0 = time.perf_counter()
        clusters_cfg = self.strategy_params.get("clusters") or DEFAULT_CLUSTERS

        anomalies: list[dict[str, Any]] = []
        valid_snapshots = 0

        for snap in market_watch:
            tk = str(snap.get("ticker", "UNKNOWN"))
            px = float(snap.get("price") or 0.0)
            atr = float(snap.get("atr_14") or 0.0)
            sma200 = float(snap.get("sma_200") or 0.0)
            stoch_k = float(snap.get("stoch_k") or 0.0)
            stoch_d = float(snap.get("stoch_d") or 0.0)
            prov_sl = float(snap.get("provisional_stop_loss") or 0.0)
            prov_tp = float(snap.get("provisional_take_profit") or 0.0)

            issues = []
            if px <= 0 or atr <= 0 or sma200 <= 0:
                issues.append(f"Telemetría base inválida (Px={px}, ATR={atr}, SMA200={sma200})")
            if not (0.0 <= stoch_k <= 100.0 and 0.0 <= stoch_d <= 100.0):
                issues.append(f"Estocástico fuera de rango [0,100] (%K={stoch_k}, %D={stoch_d})")
            if prov_sl >= px or prov_tp <= px:
                issues.append(f"Geometría Bracket invertida (SL={prov_sl} >= Px={px} o TP={prov_tp} <= Px)")
            else:
                rr = (prov_tp - px) / max(0.01, px - prov_sl)
                if rr < 1.15:
                    issues.append(f"Ratio R/R provisional insuficiente ({rr:.2f}R < 1.15R)")

            if issues:
                anomalies.append(
                    {
                        "ticker": tk,
                        "cluster_id": snap.get("cluster_id") or self._resolve_ticker_cluster(tk),
                        "issues": issues,
                    }
                )
            else:
                valid_snapshots += 1

        # Auditar oportunidades activas listas para despacho
        audited_opportunities: list[dict[str, Any]] = []
        max_risk_pct = float(self.strategy_params.get("max_portfolio_risk_per_trade_pct", 0.01)) * 100.0
        for opp in active_opportunities:
            tk = str(opp.get("ticker", ""))
            entry = float(opp.get("entry_price") or 0.0)
            sizing = opp.get("order_sizing") or {}
            metrics = opp.get("financial_metrics") or {}
            sl = float(sizing.get("stop_loss_price") or 0.0)
            tp = float(sizing.get("take_profit_price") or 0.0)
            risk_pct = float(metrics.get("portfolio_risk_pct") or 0.0)
            net_rr = float(metrics.get("net_reward_risk_ratio") or 0.0)
            fk = bool(opp.get("falling_knife", False))

            checks_passed = True
            audit_notes = []
            if sl >= entry or tp <= entry:
                checks_passed = False
                audit_notes.append("ERROR CRÍTICO: Niveles SL/TP mal ordenados respecto a entrada.")
            if risk_pct > (max_risk_pct + 0.15):
                checks_passed = False
                audit_notes.append(f"Exceso de riesgo por operación ({risk_pct:.2f}% > límite {max_risk_pct:.2f}%).")
            if net_rr < 1.20:
                checks_passed = False
                audit_notes.append(f"Ratio R:R neto insuficiente ({net_rr:.2f}R).")
            if fk:
                checks_passed = False
                audit_notes.append("Vela de capitulación (Falling Knife) detectada.")
            if not audit_notes:
                audit_notes.append(
                    f"Cálculo validado: Riesgo {risk_pct:.2f}% (<=1%), R:R Neto {net_rr:.2f}x, SL ${sl:.2f} / TP ${tp:.2f}."
                )

            audited_opportunities.append(
                {
                    "ticker": tk,
                    "cluster_id": opp.get("cluster_id") or self._resolve_ticker_cluster(tk),
                    "quant_score": opp.get("quant_score", 0),
                    "audit_verdict": "VERIFIED_OK" if checks_passed else "MATH_OR_RISK_VETO",
                    "checks_passed": checks_passed,
                    "notes": audit_notes,
                }
            )

        # Mapa de salud de los 8 clústeres
        by_cluster: dict[str, list[dict[str, Any]]] = {cid: [] for cid in clusters_cfg.keys()}
        for snap in market_watch:
            cid = snap.get("cluster_id") or self._resolve_ticker_cluster(str(snap.get("ticker", "")))
            by_cluster.setdefault(cid, []).append(snap)

        cluster_health_matrix: list[dict[str, Any]] = []
        for cid, items in by_cluster.items():
            if not items:
                continue
            total = len(items)
            advancing = sum(1 for x in items if float(x.get("ret_1d_pct") or 0.0) >= 0.0)
            bullish_structure = sum(1 for x in items if "BULLISH" in str(x.get("market_regime", "")))
            oversold_cnt = sum(1 for x in items if float(x.get("stoch_k") or 50.0) <= 30.0)
            falling_knives = sum(1 for x in items if bool(x.get("falling_knife", False)))
            avg_score = round(sum(float(x.get("quant_score") or 50.0) for x in items) / total, 1)
            avg_k = round(sum(float(x.get("stoch_k") or 50.0) for x in items) / total, 1)
            avg_ret_1d = round(sum(float(x.get("ret_1d_pct") or 0.0) for x in items) / total, 2)

            best_item = max(items, key=lambda x: float(x.get("quant_score") or 0.0))
            if oversold_cnt >= 4 and avg_score >= 52:
                status_badge = "ZONA_SOBREVENTA_ACTIVA"
            elif avg_ret_1d <= -1.5 or falling_knives >= 3:
                status_badge = "PRESION_CORRECTIVA"
            elif bullish_structure >= (total * 0.6) and avg_score >= 60:
                status_badge = "FLUJO_ALCISTA_SANO"
            else:
                status_badge = "NEUTRAL_SELECTIVO"

            bench_info = CLUSTER_SECTOR_BENCHMARKS.get(cid, {"sector_etf": "SPY", "sector_name": cid})
            cluster_health_matrix.append(
                {
                    "cluster_id": cid,
                    "sector_name": bench_info["sector_name"],
                    "benchmark_etf": bench_info["sector_etf"],
                    "tickers_audited": total,
                    "advancing_count": advancing,
                    "advancing_pct": round((advancing / total) * 100.0, 1),
                    "bullish_regime_count": bullish_structure,
                    "oversold_Zone_count": oversold_cnt,
                    "falling_knives_count": falling_knives,
                    "avg_quant_score": avg_score,
                    "avg_stoch_k": avg_k,
                    "avg_ret_1d_pct": avg_ret_1d,
                    "top_ticker": best_item.get("ticker"),
                    "top_ticker_score": best_item.get("quant_score"),
                    "status_badge": status_badge,
                }
            )

        # Radar de Near-Triggers (activos en zona de sobreventa %K <= 32 convergiendo al cruce)
        near_triggers: list[dict[str, Any]] = []
        for snap in market_watch:
            k = float(snap.get("stoch_k") or 100.0)
            d = float(snap.get("stoch_d") or 100.0)
            score = int(snap.get("quant_score") or 0)
            fk = bool(snap.get("falling_knife", False))
            spread_kd = round(k - d, 2)
            if k <= 34.0 and not fk and score >= 45:
                if spread_kd >= 0:
                    stage = "CRUCE_ACTIVO_FILTRADO"
                    detail = f"%K ({k:.1f}) ya cruzó sobre %D ({d:.1f}); retenido por: {snap.get('rejection_reason') or 'validación secundaria'}"
                elif spread_kd >= -4.5:
                    stage = "CONVERGENCIA_INMINENTE"
                    detail = f"A solo {abs(spread_kd):.1f} pts del cruce alcista (%K={k:.1f} vs %D={d:.1f}) en sobreventa."
                else:
                    stage = "EN_ZONA_SOBREVENTA"
                    detail = f"En zona de descuento (%K={k:.1f}, %D={d:.1f}), esperando giro de momento."

                near_triggers.append(
                    {
                        "ticker": snap.get("ticker"),
                        "cluster_id": snap.get("cluster_id") or self._resolve_ticker_cluster(str(snap.get("ticker", ""))),
                        "price": snap.get("price"),
                        "stoch_k": round(k, 1),
                        "stoch_d": round(d, 1),
                        "kd_spread": spread_kd,
                        "quant_score": score,
                        "market_regime": snap.get("market_regime"),
                        "stage": stage,
                        "audit_note": detail,
                    }
                )

        near_triggers.sort(key=lambda x: (0 if x["stage"] == "CONVERGENCIA_INMINENTE" else 1, -x["quant_score"], x["stoch_k"]))

        # Auditoría de correlación cruzada con posiciones activas en cartera
        correlation_warnings: list[dict[str, Any]] = []
        active_pos_clusters: dict[str, list[str]] = {}
        for pos in active_positions:
            sym = str(pos.get("symbol", "")).upper()
            if sym:
                cid = self._resolve_ticker_cluster(sym)
                active_pos_clusters.setdefault(cid, []).append(sym)

        for opp in active_opportunities:
            tk = str(opp.get("ticker", "")).upper()
            cid = opp.get("cluster_id") or self._resolve_ticker_cluster(tk)
            if cid in active_pos_clusters and tk not in active_pos_clusters[cid]:
                correlation_warnings.append(
                    {
                        "severity": "WARNING",
                        "cluster_id": cid,
                        "new_ticker": tk,
                        "existing_positions": active_pos_clusters[cid],
                        "message": (
                            f"Alerta de concentración sectorial: {tk} pertenece a {cid}, donde ya mantienes "
                            f"posición abierta en {active_pos_clusters[cid]}. Riesgo de beta correlacionada."
                        ),
                    }
                )

        total_audited = len(market_watch)
        integrity_pct = round((valid_snapshots / total_audited) * 100.0, 1) if total_audited > 0 else 100.0
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        result = {
            "subagent_id": "SUBAGENT_11_CLUSTER_AUDITOR",
            "subagent_name": "Subagente #11 · Auditor Cuantitativo de Clústeres y Pre-Trade",
            "status": "ACTIVE_VERIFIED" if len(anomalies) == 0 else "ANOMALIES_DETECTED",
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "execution_latency_ms": latency_ms,
            "total_assets_audited": total_audited,
            "clusters_audited": len(cluster_health_matrix),
            "math_integrity": {
                "valid_snapshots": valid_snapshots,
                "anomalies_detected": len(anomalies),
                "integrity_score_pct": integrity_pct,
                "anomalies": anomalies[:10],
            },
            "audited_opportunities": audited_opportunities,
            "cluster_health_matrix": cluster_health_matrix,
            "near_triggers_radar": near_triggers[:12],
            "correlation_warnings": correlation_warnings,
        }
        self.last_cluster_audit = result
        return result

    # =====================================================================
    # SUBAGENTE #11: GUARDIÁN DINÁMICO DE POSICIONES ACTIVAS Y RÉGIMEN
    # =====================================================================

    def _fetch_realtime_market_and_position_telemetry(
        self, active_symbols: list[str]
    ) -> dict[str, Any]:
        """
        Descarga con caché inteligente (TTL 15s) los indicadores en tiempo real:
        - Benchmarks macro y sectoriales: SPY, QQQ, SOXX, XLK, XLF, XLV, XLI, IWM, ^VIX, ^TNX.
        - Velas diarias (15d) e intradía de 15 minutos (5d) para los activos con posición abierta en Alpaca.
        """
        now = time.time()
        cached = self._telemetry_cache
        cache_key = ",".join(sorted(active_symbols))
        if (
            now - float(cached.get("timestamp", 0.0)) < 15.0
            and cached.get("key") == cache_key
            and cached.get("data")
        ):
            return cached["data"]

        import pandas as pd
        import yfinance as yf

        benchmarks = ["SPY", "QQQ", "SOXX", "XLK", "XLF", "XLV", "XLI", "IWM", "^VIX", "^TNX"]
        all_daily_tickers = list(dict.fromkeys(active_symbols + benchmarks))
        intra_tickers = list(dict.fromkeys(active_symbols + ["SPY", "QQQ", "SOXX"]))

        t0 = time.perf_counter()
        daily_metrics: dict[str, dict[str, Any]] = {}
        intraday_metrics: dict[str, dict[str, Any]] = {}

        try:
            from ta.momentum import RSIIndicator, StochasticOscillator
            from ta.trend import ADXIndicator, EMAIndicator, SMAIndicator
            from ta.volatility import AverageTrueRange

            raw_daily = yf.download(
                all_daily_tickers,
                period="1y",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            for tk in all_daily_tickers:
                try:
                    df = raw_daily if len(all_daily_tickers) == 1 else raw_daily[tk]
                    df = df.dropna(subset=["Close", "High", "Low", "Open"])
                    if len(df) < 2:
                        continue
                    last_row = df.iloc[-1]
                    prev_row = df.iloc[-2]
                    c = float(last_row["Close"])
                    o = float(last_row["Open"])
                    h = float(last_row["High"])
                    l = float(last_row["Low"])
                    prev_c = float(prev_row["Close"])
                    ret_1d = ((c - prev_c) / prev_c) * 100.0 if prev_c > 0 else 0.0
                    intra_chg = ((c - o) / o) * 100.0 if o > 0 else 0.0
                    clv = ((c - l) / (h - l)) if (h - l) > 0 else 0.5

                    ret_5d = 0.0
                    if len(df) >= 6:
                        c5 = float(df.iloc[-6]["Close"])
                        if c5 > 0:
                            ret_5d = ((c - c5) / c5) * 100.0

                    # Si es un activo en cartera activa, calcular sus indicadores técnicos exactos al día de hoy
                    live_ind: dict[str, Any] = {}
                    if tk in active_symbols and len(df) >= 55:
                        close_s, high_s, low_s = df["Close"], df["High"], df["Low"]
                        ema20_val = float(EMAIndicator(close=close_s, window=20).ema_indicator().iloc[-1])
                        sma50_val = float(SMAIndicator(close=close_s, window=50).sma_indicator().iloc[-1])
                        sma200_val = (
                            float(SMAIndicator(close=close_s, window=200).sma_indicator().iloc[-1])
                            if len(df) >= 200
                            else sma50_val
                        )
                        atr14_val = float(AverageTrueRange(high=high_s, low=low_s, close=close_s, window=14).average_true_range().iloc[-1])
                        stoch_ind = StochasticOscillator(high=high_s, low=low_s, close=close_s, window=14, smooth_window=4)
                        stoch_k_val = float(stoch_ind.stoch().iloc[-1])
                        stoch_d_val = float(stoch_ind.stoch_signal().iloc[-1])
                        rsi14_val = float(RSIIndicator(close=close_s, window=14).rsi().iloc[-1])
                        adx_ind = ADXIndicator(high=high_s, low=low_s, close=close_s, window=14)
                        plus_di_val = float(adx_ind.adx_pos().iloc[-1])
                        minus_di_val = float(adx_ind.adx_neg().iloc[-1])
                        vol_20_avg = float(df["Volume"].tail(20).mean()) if "Volume" in df else 1.0
                        rvol_val = float(last_row["Volume"]) / vol_20_avg if vol_20_avg > 0 else 1.0
                        live_ind = {
                            "ema_20": round(ema20_val, 2),
                            "sma_50": round(sma50_val, 2),
                            "sma_200": round(sma200_val, 2),
                            "atr_14": round(atr14_val, 2),
                            "stoch_k": round(stoch_k_val, 2),
                            "stoch_d": round(stoch_d_val, 2),
                            "rsi_14": round(rsi14_val, 1),
                            "plus_di": round(plus_di_val, 1),
                            "minus_di": round(minus_di_val, 1),
                            "rvol_20": round(rvol_val, 2),
                        }

                    # Recopilar serie reciente de precios diarios para reconstrucción MFE/MAE
                    recent_bars = []
                    for idx, r in df.tail(10).iterrows():
                        recent_bars.append(
                            {
                                "date": idx.strftime("%Y-%m-%d"),
                                "open": round(float(r["Open"]), 2),
                                "high": round(float(r["High"]), 2),
                                "low": round(float(r["Low"]), 2),
                                "close": round(float(r["Close"]), 2),
                            }
                        )

                    daily_metrics[tk] = {
                        "price": round(c, 2),
                        "open": round(o, 2),
                        "high": round(h, 2),
                        "low": round(l, 2),
                        "prev_close": round(prev_c, 2),
                        "ret_1d_pct": round(ret_1d, 2),
                        "ret_5d_pct": round(ret_5d, 2),
                        "intraday_from_open_pct": round(intra_chg, 2),
                        "clv": round(clv, 2),
                        "recent_bars": recent_bars,
                        "live_indicators": live_ind,
                    }
                except Exception:
                    continue
        except Exception as exc:
            logger.warning(f"[Subagent 11] Error descargando telemetría diaria: {exc}")

        try:
            raw_intra = yf.download(
                intra_tickers,
                period="5d",
                interval="15m",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            for tk in intra_tickers:
                try:
                    df_i = raw_intra if len(intra_tickers) == 1 else raw_intra[tk]
                    df_i = df_i.dropna(subset=["Close"])
                    if len(df_i) < 8:
                        continue
                    last_day = df_i.index[-1].date()
                    today_df = df_i[df_i.index.date == last_day]
                    if today_df.empty:
                        today_df = df_i.tail(16)

                    typ_px = (today_df["High"] + today_df["Low"] + today_df["Close"]) / 3.0
                    vol_sum = float(today_df["Volume"].sum())
                    vwap = float((typ_px * today_df["Volume"]).sum() / vol_sum) if vol_sum > 0 else float(today_df["Close"].iloc[-1])

                    ema20_series = df_i["Close"].ewm(span=20, adjust=False).mean()
                    ema20_now = float(ema20_series.iloc[-1])
                    ema20_prev = float(ema20_series.iloc[-4]) if len(ema20_series) >= 4 else ema20_now
                    ema20_slope = ((ema20_now - ema20_prev) / ema20_prev) * 100.0 if ema20_prev > 0 else 0.0

                    last_px = float(df_i["Close"].iloc[-1])
                    vwap_spread_pct = ((last_px - vwap) / vwap) * 100.0 if vwap > 0 else 0.0
                    last_4_low = float(df_i["Low"].tail(4).min())
                    last_4_high = float(df_i["High"].tail(4).max())

                    intraday_metrics[tk] = {
                        "last_price_15m": round(last_px, 2),
                        "vwap_15m": round(vwap, 2),
                        "above_vwap_15m": bool(last_px >= vwap),
                        "vwap_spread_pct": round(vwap_spread_pct, 2),
                        "ema20_15m": round(ema20_now, 2),
                        "ema20_15m_slope_pct": round(ema20_slope, 2),
                        "last_1h_low": round(last_4_low, 2),
                        "last_1h_high": round(last_4_high, 2),
                    }
                except Exception:
                    continue
        except Exception as exc:
            logger.warning(f"[Subagent 11] Error descargando telemetría 15m: {exc}")

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        payload = {
            "daily": daily_metrics,
            "intraday_15m": intraday_metrics,
            "fetch_latency_ms": elapsed_ms,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        self._telemetry_cache = {"timestamp": now, "key": cache_key, "data": payload}
        return payload

    def evaluate_active_positions_and_regime(
        self,
        active_positions: list[dict[str, Any]],
        market_watch: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Subagente #11 (ActivePositionRegimeSubagent):
        Analiza el estado actual del mercado, volatilidad (^VIX), bonos (^TNX), ETFs sectoriales (SOXX, QQQ, XLK, XLF)
        y la evolución intradía/multisesión de las posiciones abiertas en Alpaca.
        Detecta crecimientos bruscos (para elevar expectativas de profit y subir SL) o correcciones bruscas
        (como el jueves y viernes en NVDA y el sector tecnológico) para ajustar SL y TP a la realidad del mercado.
        """
        t0 = time.perf_counter()
        active_symbols = [str(p.get("symbol", "")).strip().upper() for p in active_positions if p.get("symbol")]
        telemetry = self._fetch_realtime_market_and_position_telemetry(active_symbols)
        daily_map = telemetry.get("daily", {})
        intra_map = telemetry.get("intraday_15m", {})

        bars_5m_map: dict[str, Any] = {}
        bars_15m_map: dict[str, Any] = {}
        if active_symbols:
            try:
                from three_tricks_engine import fetch_intraday_bars_batch

                bars_5m_map = fetch_intraday_bars_batch(active_symbols, interval="5m", period="2d")
                bars_15m_map = fetch_intraday_bars_batch(active_symbols, interval="15m", period="5d")
            except Exception as e:
                logger.warning(f"[AUDIT AGENT] Error obteniendo barras 5m/15m para el Guardián: {e}")

        mw_by_ticker = {str(m.get("ticker", "")).upper(): m for m in market_watch}

        # Resumen de variables macro/sectoriales en tiempo real
        vix_data = daily_map.get("^VIX", {})
        tnx_data = daily_map.get("^TNX", {})
        spy_data = daily_map.get("SPY", {})
        qqq_data = daily_map.get("QQQ", {})
        soxx_data = daily_map.get("SOXX", {})

        vix_level = float(vix_data.get("price") or 18.5)
        vix_ret_1d = float(vix_data.get("ret_1d_pct") or 0.0)
        vix_ret_5d = float(vix_data.get("ret_5d_pct") or 0.0)
        soxx_ret_1d = float(soxx_data.get("ret_1d_pct") or 0.0)
        soxx_ret_5d = float(soxx_data.get("ret_5d_pct") or 0.0)
        qqq_ret_1d = float(qqq_data.get("ret_1d_pct") or 0.0)
        qqq_ret_5d = float(qqq_data.get("ret_5d_pct") or 0.0)

        # Clasificar el pulso global de mercado
        if (soxx_ret_5d <= -3.5 or qqq_ret_5d <= -1.8) or (vix_level >= 19.0 and vix_ret_5d >= 5.0):
            global_pulse = "TECH_CORRECTION_PRESSURE"
            global_pulse_label = (
                f"Corrección Tecnológica Detectada (SOXX 5d {soxx_ret_5d:+.2f}%, QQQ 5d {qqq_ret_5d:+.2f}%, VIX en {vix_level:.2f})"
            )
        elif soxx_ret_1d >= 1.5 and qqq_ret_1d >= 0.8 and vix_ret_1d <= -3.0:
            global_pulse = "STRONG_BULLISH_EXPANSION"
            global_pulse_label = (
                f"Impulso Alcista Institucional (SOXX {soxx_ret_1d:+.2f}%, QQQ {qqq_ret_1d:+.2f}%, VIX cayendo {vix_ret_1d:+.2f}%)"
            )
        else:
            global_pulse = "ROTATIONAL_SELECTIVE"
            global_pulse_label = (
                f"Mercado Selectivo / Rotacional (SPY {spy_data.get('ret_1d_pct', 0.0):+.2f}%, QQQ {qqq_ret_1d:+.2f}%, VIX {vix_level:.2f})"
            )

        positions_diagnostics: list[dict[str, Any]] = []
        excursions_modified = False

        for pos in active_positions:
            sym = str(pos.get("symbol", "")).strip().upper()
            if not sym:
                continue

            qty = float(pos.get("qty") or 0.0)
            entry_px = float(pos.get("avg_entry_price") or 0.0)
            curr_px = float(pos.get("current_price") or entry_px)
            curr_sl = float(pos.get("stop_loss_price") or 0.0) if pos.get("stop_loss_price") else None
            curr_tp = float(pos.get("take_profit_price") or 0.0) if pos.get("take_profit_price") else None
            unrealized_pl = float(pos.get("unrealized_pl") or 0.0)
            unrealized_plpc = float(pos.get("unrealized_plpc") or 0.0)

            mw_snap = mw_by_ticker.get(sym, {})
            d_snap = daily_map.get(sym, {})
            i_snap = intra_map.get(sym, {})

            cluster_id = mw_snap.get("cluster_id") or self._resolve_ticker_cluster(sym)
            bench_cfg = CLUSTER_SECTOR_BENCHMARKS.get(
                cluster_id, {"sector_etf": "SOXX", "secondary_etf": "QQQ", "sector_name": "Tecnología"}
            )
            sec_etf = bench_cfg["sector_etf"]
            sec_data = daily_map.get(sec_etf, soxx_data)

            # Encontrar fecha de apertura en el libro de operaciones (por defecto 2026-10-17)
            entry_date_str = "2026-10-17"
            initial_risk_per_share = round(entry_px - curr_sl, 2) if (curr_sl and entry_px > curr_sl) else round(entry_px * 0.042, 2)
            for tr in self.trades_cache:
                if str(tr.get("ticker", "")).upper() == sym and tr.get("lifecycle", {}).get("state") == "OPEN_OR_PENDING":
                    opened_iso = str(tr.get("opened_at") or "")
                    if len(opened_iso) >= 10:
                        entry_date_str = opened_iso[:10]
                    rps = float(tr.get("risk_and_sizing", {}).get("risk_per_share") or 0.0)
                    if rps > 0:
                        initial_risk_per_share = rps
                    break

            # Detectar si ya se ejecutó una venta defensiva parcial ( ~40% ) con beneficio realizado en este activo
            banked_defensive_profit_usd = 0.0
            banked_defensive_shares = 0
            banked_defensive_exit_price: Optional[float] = None
            for tr in self.trades_cache:
                if str(tr.get("ticker", "")).upper() != sym:
                    continue
                lc = tr.get("lifecycle", {})
                if lc.get("state") == "CLOSED" and float(lc.get("realized_pnl_usd") or 0.0) > 0:
                    closed_iso = str(tr.get("closed_at") or tr.get("opened_at") or "")
                    if not closed_iso or closed_iso[:10] >= entry_date_str:
                        banked_defensive_profit_usd += float(lc.get("realized_pnl_usd") or 0.0)
                        banked_defensive_shares += int(tr.get("risk_and_sizing", {}).get("shares") or 0)
                        if banked_defensive_exit_price is None and lc.get("exit_price") is not None:
                            banked_defensive_exit_price = float(lc["exit_price"])
            banked_defensive_profit_usd = round(banked_defensive_profit_usd, 2)

            # Reconstruir MFE (Maximum Favorable Excursion) y MAE desde la fecha de entrada usando las barras diarias reales
            recent_bars = d_snap.get("recent_bars", [])
            bars_since_entry = [b for b in recent_bars if b["date"] >= entry_date_str]
            if not bars_since_entry and recent_bars:
                bars_since_entry = recent_bars[-5:]

            exc_rec = self.position_excursions.get(sym, {})
            peak_px = max(float(exc_rec.get("peak_price", entry_px)), curr_px)
            peak_date = exc_rec.get("peak_date", entry_date_str)
            trough_px = min(float(exc_rec.get("trough_price", entry_px)), curr_px)
            trough_date = exc_rec.get("trough_date", entry_date_str)

            # Identificar también la mayor caída en 1 o 2 días (como el jueves 22 y viernes 23)
            worst_drop_bar = None
            worst_drop_pct = 0.0
            prev_close_iter = entry_px
            for b in bars_since_entry:
                if b["high"] >= peak_px:
                    peak_px = b["high"]
                    peak_date = b["date"]
                if b["low"] <= trough_px:
                    trough_px = b["low"]
                    trough_date = b["date"]
                bar_chg = ((b["close"] - prev_close_iter) / prev_close_iter) * 100.0 if prev_close_iter > 0 else 0.0
                if bar_chg < worst_drop_pct:
                    worst_drop_pct = round(bar_chg, 2)
                    worst_drop_bar = b
                prev_close_iter = b["close"]

            mfe_usd_per_sh = round(max(0.0, peak_px - entry_px), 2)
            mfe_total_usd = round(mfe_usd_per_sh * qty, 2)
            mfe_r = round(mfe_usd_per_sh / max(0.01, initial_risk_per_share), 2)

            mae_usd_per_sh = round(min(0.0, trough_px - entry_px), 2)
            mae_r = round(mae_usd_per_sh / max(0.01, initial_risk_per_share), 2)

            current_r = round((curr_px - entry_px) / max(0.01, initial_risk_per_share), 2)
            giveback_pct = (
                round(((peak_px - curr_px) / max(0.01, peak_px - entry_px)) * 100.0, 1)
                if peak_px > entry_px + 0.20
                else 0.0
            )

            self.position_excursions[sym] = {
                "symbol": sym,
                "entry_price": entry_px,
                "entry_date": entry_date_str,
                "peak_price": peak_px,
                "peak_date": peak_date,
                "mfe_r": mfe_r,
                "mfe_total_usd": mfe_total_usd,
                "trough_price": trough_px,
                "trough_date": trough_date,
                "mae_r": mae_r,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            excursions_modified = True

            # Variables técnicas en vivo del activo (priorizando cálculo fresco en tiempo real sobre caché)
            live_ind = d_snap.get("live_indicators") or {}
            ema_20 = float(live_ind.get("ema_20") or mw_snap.get("ema_20") or (entry_px * 1.03))
            sma_50 = float(live_ind.get("sma_50") or mw_snap.get("sma_50") or (entry_px * 1.04))
            sma_200 = float(live_ind.get("sma_200") or mw_snap.get("sma_200") or (entry_px * 0.95))
            atr_14 = float(live_ind.get("atr_14") or mw_snap.get("atr_14") or (entry_px * 0.028))
            stoch_k = float(live_ind.get("stoch_k") if live_ind.get("stoch_k") is not None else (mw_snap.get("stoch_k") or 50.0))
            stoch_d = float(live_ind.get("stoch_d") if live_ind.get("stoch_d") is not None else (mw_snap.get("stoch_d") or 50.0))
            rsi_14 = float(live_ind.get("rsi_14") if live_ind.get("rsi_14") is not None else (mw_snap.get("rsi_14") or 50.0))
            plus_di = float(live_ind.get("plus_di") if live_ind.get("plus_di") is not None else (mw_snap.get("plus_di") or 20.0))
            minus_di = float(live_ind.get("minus_di") if live_ind.get("minus_di") is not None else (mw_snap.get("minus_di") or 20.0))
            di_spread = round(plus_di - minus_di, 1)
            ret_1d = float(d_snap.get("ret_1d_pct") or mw_snap.get("ret_1d_pct") or 0.0)
            ret_5d = float(d_snap.get("ret_5d_pct") or mw_snap.get("ret_5d_pct") or 0.0)
            clv_daily = float(d_snap.get("clv") or mw_snap.get("close_location_value") or 0.5)
            rvol_20 = float(live_ind.get("rvol_20") or mw_snap.get("rvol_20") or 1.0)

            vwap_15m = float(i_snap.get("vwap_15m") or curr_px)
            above_vwap_15m = bool(i_snap.get("above_vwap_15m", curr_px >= vwap_15m))
            vwap_spread_pct = float(i_snap.get("vwap_spread_pct") or 0.0)
            ema20_15m_slope = float(i_snap.get("ema20_15m_slope_pct") or 0.0)

            sec_ret_1d = float(sec_data.get("ret_1d_pct") or 0.0)
            sec_ret_5d = float(sec_data.get("ret_5d_pct") or 0.0)
            rel_strength_1d = round(ret_1d - sec_ret_1d, 2)
            rel_strength_5d = round(ret_5d - sec_ret_5d, 2)

            # -----------------------------------------------------------------
            # MOTOR DE DECISIÓN DEL GUARDIÁN VIVO (V50: BE+, Trailing MFE, Early Abort, EOD)
            # -----------------------------------------------------------------
            from position_guardian import evaluate_position_guardian, extract_intraday_guardian_signals

            action_type = "NONE"
            regime_code = "HOLD_STEADY"
            urgency = "LOW"
            action_required = False
            recommended_sl = curr_sl
            recommended_tp = curr_tp
            headline = "Posición evolucionando dentro de los márgenes esperados"
            why_proposed = ""
            action_summary = "Mantener niveles OCO actuales sin cambios."
            quantitative_evidence: list[str] = []
            scale_out_plan: Optional[dict[str, Any]] = None
            net_zero_shield: Optional[dict[str, Any]] = None

            overhead_wall = min([w for w in [ema_20, sma_50] if w > curr_px] or [sma_50])
            structural_ceiling_tp = round(overhead_wall * 0.997, 2)
            if structural_ceiling_tp <= curr_px + 0.50:
                structural_ceiling_tp = round(curr_px + (0.75 * atr_14), 2)

            if banked_defensive_profit_usd > 0 and qty > 0:
                regime_code = "NET_ZERO_SHIELD_RUNNER_MODE"
                sl_net_zero = round(entry_px - (banked_defensive_profit_usd / qty), 2)
                recommended_sl = round(max(curr_sl or 0.0, sl_net_zero), 2)
                if curr_tp is not None and entry_px < curr_tp <= (sma_50 + 0.25) and curr_tp >= curr_px + 1.00:
                    recommended_tp = round(curr_tp, 2)
                else:
                    recommended_tp = structural_ceiling_tp

                sl_diff = abs((curr_sl or 0.0) - recommended_sl)
                tp_diff = abs((curr_tp or 0.0) - recommended_tp)
                action_required = (sl_diff >= 0.05) or (tp_diff >= 0.25)
                action_type = "ADJUST_SL" if action_required else "NONE"
                urgency = "HIGH" if action_required else "LOW"

                worst_case_runner_loss = round((entry_px - recommended_sl) * qty, 2)
                worst_case_net_total = round(banked_defensive_profit_usd - worst_case_runner_loss, 2)
                target_net_total = round(banked_defensive_profit_usd + (recommended_tp - entry_px) * qty, 2)
                net_zero_shield = {
                    "active": True,
                    "banked_realized_pnl_usd": banked_defensive_profit_usd,
                    "banked_shares": banked_defensive_shares,
                    "banked_exit_price": banked_defensive_exit_price,
                    "zero_loss_sl_price": sl_net_zero,
                    "realistic_ceiling_tp_price": recommended_tp,
                    "worst_case_net_total_usd": worst_case_net_total,
                    "target_net_total_usd": target_net_total,
                }
                headline = (
                    f"Escudo Pérdida Cero en {sym}: +${banked_defensive_profit_usd:.2f} cobrados "
                    f"| SL ${recommended_sl:.2f} → TP ${recommended_tp:.2f}"
                )
                why_proposed = (
                    f"Venta parcial ya realizada con +${banked_defensive_profit_usd:.2f}; "
                    f"SL neto cero fijado en ${recommended_sl:.2f}."
                )
                action_summary = (
                    f"Fijar SL en ${recommended_sl:.2f} y TP en ${recommended_tp:.2f}."
                    if action_required
                    else headline
                )
                quantitative_evidence = [
                    f"Beneficio realizado parcial: +${banked_defensive_profit_usd:.2f}",
                    f"SL Pérdida Nula: ${sl_net_zero:.2f} | Peor escenario global: ${worst_case_net_total:+.2f}",
                ]
            else:
                intraday_sig = extract_intraday_guardian_signals(bars_5m_map.get(sym), bars_15m_map.get(sym))
                if not intraday_sig.get("vwap") and vwap_15m > 0:
                    intraday_sig["vwap"] = vwap_15m
                    intraday_sig["below_vwap"] = bool(curr_px < vwap_15m)
                guardian_res = evaluate_position_guardian(
                    symbol=sym,
                    qty=qty,
                    entry_price=entry_px,
                    current_price=curr_px,
                    current_sl=curr_sl,
                    current_tp=curr_tp,
                    peak_price=peak_px,
                    initial_risk_per_share=initial_risk_per_share,
                    unrealized_pl=unrealized_pl,
                    clv_daily=clv_daily,
                    intraday_sig=intraday_sig,
                    params=self.strategy_params,
                )
                action_type = guardian_res["action_type"]
                regime_code = guardian_res["regime_code"]
                urgency = guardian_res["urgency"]
                action_required = bool(guardian_res["action_required"])
                recommended_sl = guardian_res["recommended_sl"]
                recommended_tp = guardian_res["recommended_tp"]
                headline = guardian_res["headline"]
                why_proposed = guardian_res["why_proposed"]
                action_summary = guardian_res["action_summary"]
                quantitative_evidence = guardian_res["quantitative_evidence"] + [
                    f"Sector {sec_etf}: {sec_ret_1d:+.2f}% hoy ({sec_ret_5d:+.2f}% 5d) | ^VIX: {vix_level:.2f}"
                ]

            current_max_loss_usd = round((entry_px - (curr_sl or entry_px)) * qty, 2)
            proposed_max_loss_usd = round((entry_px - (recommended_sl or entry_px)) * qty, 2)
            current_target_profit_usd = round(((curr_tp or entry_px) - entry_px) * qty, 2)
            proposed_target_profit_usd = round(((recommended_tp or entry_px) - entry_px) * qty, 2)

            positions_diagnostics.append(
                {
                    "symbol": sym,
                    "cluster_id": cluster_id,
                    "sector_name": bench_cfg["sector_name"],
                    "sector_etf": sec_etf,
                    "qty": qty,
                    "entry_price": entry_px,
                    "entry_date": entry_date_str,
                    "current_price": curr_px,
                    "unrealized_pl_usd": unrealized_pl,
                    "unrealized_pl_pct": unrealized_plpc,
                    "current_r_multiple": current_r,
                    "excursion_telemetry": {
                        "peak_price_since_entry": peak_px,
                        "peak_date": peak_date,
                        "mfe_r": mfe_r,
                        "mfe_total_usd": mfe_total_usd,
                        "trough_price_since_entry": trough_px,
                        "trough_date": trough_date,
                        "mae_r": mae_r,
                        "profit_giveback_pct": giveback_pct,
                        "worst_single_day_drop_pct": worst_drop_pct,
                        "worst_single_day_date": worst_drop_bar["date"] if worst_drop_bar else None,
                    },
                    "realtime_variables": {
                        "ema_20": round(ema_20, 2),
                        "sma_50": round(sma_50, 2),
                        "sma_200": round(sma_200, 2),
                        "atr_14": round(atr_14, 2),
                        "stoch_k": round(stoch_k, 2),
                        "stoch_d": round(stoch_d, 2),
                        "rsi_14": round(rsi_14, 1),
                        "plus_di": round(plus_di, 1),
                        "minus_di": round(minus_di, 1),
                        "di_spread": di_spread,
                        "clv_daily": round(clv_daily, 2),
                        "rvol_20": round(rvol_20, 2),
                        "vwap_15m": vwap_15m,
                        "above_vwap_15m": above_vwap_15m,
                        "vwap_spread_pct": vwap_spread_pct,
                        "ema20_15m_slope_pct": ema20_15m_slope,
                        "sector_etf_ret_1d_pct": sec_ret_1d,
                        "sector_etf_ret_5d_pct": sec_ret_5d,
                        "relative_strength_vs_sector_1d": rel_strength_1d,
                        "relative_strength_vs_sector_5d": rel_strength_5d,
                        "vix_level": vix_level,
                        "vix_roc_1d_pct": vix_ret_1d,
                    },
                    "regime_code": regime_code,
                    "action_type": action_type,
                    "urgency": urgency,
                    "action_required": action_required,
                    "headline": headline,
                    "net_zero_shield": net_zero_shield,
                    "current_levels": {
                        "stop_loss_price": curr_sl,
                        "take_profit_price": curr_tp,
                        "max_loss_usd": current_max_loss_usd,
                        "target_profit_usd": current_target_profit_usd,
                    },
                    "recommended_levels": {
                        "stop_loss_price": recommended_sl,
                        "take_profit_price": recommended_tp,
                        "max_loss_usd": proposed_max_loss_usd,
                        "target_profit_usd": proposed_target_profit_usd,
                        "risk_reduction_usd": round(current_max_loss_usd - proposed_max_loss_usd, 2),
                        "scale_out_plan": scale_out_plan,
                    },
                    "proposal_explanation": {
                        "why_proposed": why_proposed,
                        "action_summary": action_summary,
                        "quantitative_evidence": quantitative_evidence,
                        "decision_helper_note": (
                            "Al confirmar este ajuste dinámico, el motor reemplazará de forma atómica las órdenes "
                            "Limit (Take Profit) y Stop (Stop Loss) activas en Alpaca Paper Trading sin cerrar la posición."
                        ),
                    },
                }
            )

        if excursions_modified:
            self._save_json_dict(POSITION_EXCURSIONS_FILE, self.position_excursions)

        total_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        result = {
            "subagent_id": "SUBAGENT_12_ACTIVE_POSITION_GUARDIAN",
            "subagent_name": "Subagente #12 · Guardián Dinámico de Posiciones y Régimen de Mercado",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "execution_latency_ms": total_latency_ms,
            "auto_adapt_enabled": self.auto_adapt_positions,
            "global_market_pulse": {
                "pulse_code": global_pulse,
                "headline": global_pulse_label,
                "spy_ret_1d_pct": spy_data.get("ret_1d_pct", 0.0),
                "qqq_ret_1d_pct": qqq_ret_1d,
                "qqq_ret_5d_pct": qqq_ret_5d,
                "soxx_ret_1d_pct": soxx_ret_1d,
                "soxx_ret_5d_pct": soxx_ret_5d,
                "vix_level": vix_level,
                "vix_roc_1d_pct": vix_ret_1d,
                "vix_roc_5d_pct": vix_ret_5d,
                "tnx_10y_yield": tnx_data.get("price", 0.0),
            },
            "active_positions_count": len(positions_diagnostics),
            "actions_pending_count": sum(1 for p in positions_diagnostics if p["action_required"]),
            "positions_diagnostics": positions_diagnostics,
            "recent_adjustments_audit_log": self.dynamic_adjustments_log[:25],
        }
        self.last_active_positions_audit = result
        return result

    def set_auto_adapt_positions(self, enabled: bool) -> dict[str, Any]:
        """Activa o desactiva el modo piloto automático del Subagente #11 para adaptar posiciones activas."""
        self.auto_adapt_positions = bool(enabled)
        self.save_strategy_params({"auto_adapt_positions": self.auto_adapt_positions}, updated_by="SUBAGENT_11_CONFIG")
        return {"auto_adapt_positions": self.auto_adapt_positions}

    def record_dynamic_adjustment(
        self,
        symbol: str,
        trigger_mode: str,
        regime_code: str,
        previous_sl: Optional[float],
        new_sl: Optional[float],
        previous_tp: Optional[float],
        new_tp: Optional[float],
        headline: str,
        why_proposed: str,
        quantitative_evidence: list[str],
        alpaca_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Guarda en disco un registro inmutable de auditoría cada vez que el Subagente #11
        (en modo automático o en 1-Clic manual) modifica los niveles SL/TP de una posición viva.
        """
        adj_id = f"dadj-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        entry = {
            "adjustment_id": adj_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol.upper(),
            "trigger_mode": trigger_mode,  # "AUTO_SUBAGENT_11" | "MANUAL_1CLICK_CONFIRMED"
            "regime_code": regime_code,
            "previous_levels": {
                "stop_loss_price": previous_sl,
                "take_profit_price": previous_tp,
            },
            "new_levels": {
                "stop_loss_price": new_sl,
                "take_profit_price": new_tp,
            },
            "headline": headline,
            "why_proposed": why_proposed,
            "quantitative_evidence": quantitative_evidence,
            "alpaca_execution": alpaca_result,
        }
        self.dynamic_adjustments_log.insert(0, entry)
        self._save_json_list(DYNAMIC_ADJUSTMENTS_LOG_FILE, self.dynamic_adjustments_log)
        logger.info(
            f"[SUBAGENT 11 AUDIT] Ajuste dinámico registrado ({trigger_mode}) en {symbol}: "
            f"SL ${previous_sl} -> ${new_sl} | TP ${previous_tp} -> ${new_tp} | Motivo: {headline}"
        )
        return entry


audit_agent = IndependentAuditAgent()
