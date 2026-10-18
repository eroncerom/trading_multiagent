import asyncio
import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import ADXIndicator, EMAIndicator, SMAIndicator
from ta.volatility import AverageTrueRange

import ai_advisor
from audit_agent import AUDIT_DIR, DEFAULT_CLUSTERS, audit_agent, get_adaptive_allocation_pct

MARKET_WATCH_CACHE_FILE = AUDIT_DIR / "market_watch_latest.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("quant-engine")

ENV_FILE_PATH = "/app/.env" if os.path.exists("/app/.env") else ".env"


def get_all_cluster_tickers() -> list[str]:
    """Obtiene la lista completa de los 60 tickers distribuidos en los 4 clústeres."""
    clusters = audit_agent.strategy_params.get("clusters") or DEFAULT_CLUSTERS
    tickers: list[str] = []
    for cl_list in clusters.values():
        for t in cl_list:
            if t.upper() not in tickers:
                tickers.append(t.upper())
    return tickers


def reload_env_config() -> dict[str, Any]:
    """Recarga variables de entorno desde .env en caliente sin reiniciar el contenedor."""
    load_dotenv(ENV_FILE_PATH, override=True)
    all_cluster_tickers = get_all_cluster_tickers()
    default_tickers_raw = os.getenv("SCANNER_DEFAULT_TICKERS", "")
    if default_tickers_raw.strip():
        default_tickers = [t.strip().upper() for t in default_tickers_raw.split(",") if t.strip()]
    else:
        default_tickers = all_cluster_tickers

    return {
        "max_risk_pct": float(os.getenv("MAX_PORTFOLIO_RISK_PCT", 0.01)),
        "max_position_allocation_pct": float(os.getenv("MAX_POSITION_ALLOCATION_PCT", 0.35)),
        "commission_per_order": float(os.getenv("BROKER_COMMISSION_PER_ORDER", 0.35)),
        "max_comm_ratio": float(os.getenv("MAX_COMMISSION_TO_PROFIT_RATIO", 0.10)),
        "default_portfolio_value": float(os.getenv("DEFAULT_PORTFOLIO_VALUE", 1000.0)),
        "default_interval_seconds": int(os.getenv("SCANNER_INTERVAL_SECONDS", 10)),
        "default_tickers": default_tickers,
        "default_slippage_pct": float(os.getenv("DEFAULT_SLIPPAGE_BUFFER_PCT", 0.0015)),
        "alpaca_api_key": os.getenv("ALPACA_API_KEY", "").strip(),
        "alpaca_secret_key": os.getenv("ALPACA_SECRET_KEY", "").strip(),
        "alpaca_paper": os.getenv("ALPACA_PAPER", "true").lower() == "true",
        "n8n_webhook_url": os.getenv("N8N_OPPORTUNITY_WEBHOOK_URL", "").strip(),
        "auto_start": os.getenv("SCANNER_AUTO_START", "false").lower() == "true",
    }


# =====================================================================
# MODELOS DE DATOS (PYDANTIC)
# =====================================================================

class ScanRequest(BaseModel):
    tickers: list[str]
    portfolio_value: float = Field(default=1000.0, gt=0)
    force_test_signal: bool = False


class TradeCandidate(BaseModel):
    ticker: str
    portfolio_value: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    market_regime: str = "UNKNOWN"
    stoch_k: float = 0.0
    stoch_d: float = 0.0


class ExecuteOrderRequest(BaseModel):
    ticker: str
    shares: int = Field(..., gt=0)
    entry_limit_price: float = Field(..., gt=0)
    stop_loss_price: float = Field(..., gt=0)
    take_profit_price: float = Field(..., gt=0)
    order_type: Literal["marketable_limit", "limit", "market"] = "marketable_limit"
    slippage_buffer_pct: Optional[float] = Field(
        default=None,
        description="Colchón máximo sobre precio de entrada en marketable_limit (ej. 0.0015 = 0.15%) para asegurar fill inmediato sin perder el tren.",
    )
    time_in_force: Literal["day", "gtc"] = "gtc"


class ScannerStartRequest(BaseModel):
    tickers: Optional[list[str]] = None
    interval_seconds: int = Field(default=10, ge=5, le=3600)
    execution_mode: Literal["MANUAL", "FULL_AUTO"] = Field(
        default="MANUAL",
        description="MANUAL (espera confirmación humana 1-clic) o FULL_AUTO (ejecuta entradas automáticamente con parada de emergencia al 4% de pérdida).",
    )
    max_session_drawdown_pct: float = Field(
        default=0.04,
        ge=0.005,
        le=0.25,
        description="Pérdida máxima de cartera permitida en la sesión antes de parada automática (0.04 = 4.0%).",
    )
    auto_apply_audit_fixes: bool = Field(
        default=True,
        description="Si está activo, al terminar la sesión el Agente Auditor aplica automáticamente las correcciones de parámetros recomendadas.",
    )
    session_duration_minutes: Optional[int] = Field(
        default=None,
        ge=1,
        le=1440,
        description="Duración opcional de la sesión en minutos (ej. 120 para 2 horas). Si se omite o es null, corre indefinidamente hasta parada manual.",
    )
    portfolio_value: Optional[float] = Field(default=None, gt=0)
    auto_sync_alpaca_equity: bool = True
    force_test_signal: bool = False


class ApproveOpportunityRequest(BaseModel):
    order_type: Literal["marketable_limit", "limit", "market"] = "marketable_limit"
    slippage_buffer_pct: float = Field(default=0.0015, ge=0.0, le=0.02)
    refresh_live_price: bool = True
    override_shares: Optional[int] = Field(default=None, gt=0)
    time_in_force: Literal["day", "gtc"] = "gtc"


class AlpacaCredentialsRequest(BaseModel):
    api_key: str = Field(..., min_length=5)
    secret_key: str = Field(..., min_length=5)
    paper: bool = True


# =====================================================================
# CLIENTE Y UTILIDADES DE ALPACA PAPER TRADING
# =====================================================================

def is_real_alpaca_key(api_key: str, secret_key: str) -> bool:
    if not api_key or not secret_key:
        return False
    placeholders = ("TU_API_KEY", "TU_SECRET_KEY", "SIMULACION", "YOUR_API_KEY", "XXXX")
    return not any(p in api_key.upper() or p in secret_key.upper() for p in placeholders)


def get_alpaca_trading_client():
    """Devuelve (TradingClient | None, is_configured: bool, config_dict)."""
    cfg = reload_env_config()
    api_key = cfg["alpaca_api_key"]
    secret_key = cfg["alpaca_secret_key"]
    if not is_real_alpaca_key(api_key, secret_key):
        return None, False, cfg

    from alpaca.trading.client import TradingClient
    client = TradingClient(api_key, secret_key, paper=cfg["alpaca_paper"])
    return client, True, cfg


def fetch_alpaca_account_sync() -> dict[str, Any]:
    """Obtiene el estado real de la cuenta en Alpaca Paper o el fallback simulado."""
    client, is_configured, cfg = get_alpaca_trading_client()
    if not is_configured or client is None:
        return {
            "connected": False,
            "mode": "SIMULATION_FALLBACK",
            "setup_required": True,
            "message": "Credenciales de Alpaca Paper pendientes de configurar en .env (usando cartera simulada local).",
            "account_id": "sim-local-paper",
            "status": "ACTIVE_SIMULATED",
            "currency": "USD",
            "equity": cfg["default_portfolio_value"],
            "cash": cfg["default_portfolio_value"],
            "buying_power": cfg["default_portfolio_value"] * 2.0,
            "daytrade_count": 0,
            "trading_blocked": False,
            "account_blocked": False,
        }

    try:
        acct = client.get_account()
        return {
            "connected": True,
            "mode": "LIVE_PAPER" if cfg["alpaca_paper"] else "LIVE_BROKER",
            "setup_required": False,
            "message": "Conectado en vivo con Alpaca Paper Trading API.",
            "account_id": str(acct.id),
            "account_number": str(getattr(acct, "account_number", "")),
            "status": str(acct.status.value if hasattr(acct.status, "value") else acct.status),
            "currency": str(acct.currency),
            "equity": float(acct.equity),
            "last_equity": float(getattr(acct, "last_equity", acct.equity)),
            "daily_pnl": round(float(acct.equity) - float(getattr(acct, "last_equity", acct.equity)), 2),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "daytrade_count": int(getattr(acct, "daytrade_count", 0) or 0),
            "pattern_day_trader": bool(getattr(acct, "pattern_day_trader", False)),
            "trading_blocked": bool(acct.trading_blocked),
            "account_blocked": bool(acct.account_blocked),
        }
    except Exception as exc:
        logger.warning(f"Error consultando cuenta de Alpaca: {exc}")
        return {
            "connected": False,
            "mode": "ALPACA_AUTH_ERROR",
            "setup_required": True,
            "message": f"Error conectando a Alpaca API: {str(exc)}",
            "equity": cfg["default_portfolio_value"],
            "cash": cfg["default_portfolio_value"],
            "buying_power": cfg["default_portfolio_value"],
            "trading_blocked": False,
        }


# =====================================================================
# MOTOR MATEMÁTICO DE RIESGO ESTRICTO (REGLA DEL 1%)
# =====================================================================

def evaluate_risk_math(
    portfolio_value: float,
    entry_price: float,
    stop_loss_price: float,
    take_profit_price: float,
) -> dict[str, Any]:
    cfg = reload_env_config()
    max_risk_pct = float(
        audit_agent.strategy_params.get("max_portfolio_risk_per_trade_pct", cfg["max_risk_pct"])
    )
    max_alloc_pct, tier_label = get_adaptive_allocation_pct(
        portfolio_value, audit_agent.strategy_params
    )
    commission_per_order = cfg["commission_per_order"]
    max_comm_ratio = cfg["max_comm_ratio"]

    if entry_price <= stop_loss_price:
        return {"viable": False, "reason": "El Stop Loss debe ser inferior al precio de entrada."}

    if take_profit_price <= entry_price:
        return {"viable": False, "reason": "El Take Profit debe ser superior al precio de entrada."}

    round_trip_commission = commission_per_order * 2.0
    max_allowed_loss = portfolio_value * max_risk_pct
    risk_budget_for_shares = max_allowed_loss - round_trip_commission

    if risk_budget_for_shares <= 0:
        return {"viable": False, "reason": "Las comisiones superan el 1% de riesgo de la cartera."}

    risk_per_share = entry_price - stop_loss_price
    shares_by_risk = math.floor(risk_budget_for_shares / risk_per_share)

    # Tope de capital adaptativo según el tamaño actual de la cartera (35% en 1k-2k€, 25% en 2k-5k€, 15% en 5k-15k€, 10% en >15k€)
    max_capital_per_trade = portfolio_value * max_alloc_pct
    max_affordable_shares = math.floor((max_capital_per_trade - round_trip_commission) / entry_price)
    shares = min(shares_by_risk, max_affordable_shares)
    sizing_capped_by = (
        f"CAPITAL_CAP_{tier_label}"
        if max_affordable_shares < shares_by_risk
        else f"RISK_LOSS_BUDGET ({round(max_risk_pct * 100, 1)}%)"
    )

    if shares < 1:
        return {
            "viable": False,
            "reason": (
                f"Tamaño de posición menor a 1 acción para respetar el tope de capital "
                f"({round(max_alloc_pct*100, 1)}% = ${round(max_capital_per_trade, 2)}) o el 1% de riesgo (${round(max_allowed_loss, 2)})."
            ),
        }

    gross_profit = shares * (take_profit_price - entry_price)
    net_profit = gross_profit - round_trip_commission
    actual_max_loss = (shares * risk_per_share) + round_trip_commission
    commission_weight = round_trip_commission / gross_profit if gross_profit > 0 else 1.0
    net_reward_to_risk = net_profit / actual_max_loss if actual_max_loss > 0 else 0.0
    allocated_capital = round(shares * entry_price, 2)

    if net_profit <= round_trip_commission or commission_weight > max_comm_ratio:
        return {
            "viable": False,
            "reason": (
                f"Filtro de comisiones ({round(commission_weight*100, 1)}% > {round(max_comm_ratio*100, 0)}%) "
                f"o beneficio neto insuficiente (${round(net_profit, 2)})."
            ),
        }

    return {
        "viable": True,
        "order_sizing": {
            "shares_to_buy": shares,
            "total_capital_allocated": allocated_capital,
            "portfolio_allocation_pct": round((allocated_capital / portfolio_value) * 100.0, 2),
            "max_capital_allowed_per_trade": round(max_capital_per_trade, 2),
            "allocation_tier": tier_label,
            "sizing_capped_by": sizing_capped_by,
            "entry_limit_price": round(entry_price, 2),
            "stop_loss_price": round(stop_loss_price, 2),
            "take_profit_price": round(take_profit_price, 2),
        },
        "financial_metrics": {
            "max_loss_with_commissions": round(actual_max_loss, 2),
            "portfolio_risk_pct": round((actual_max_loss / portfolio_value) * 100, 2),
            "expected_net_profit": round(net_profit, 2),
            "net_reward_risk_ratio": round(net_reward_to_risk, 2),
            "round_trip_commission": round(round_trip_commission, 2),
            "commission_to_gross_profit_pct": round(commission_weight * 100, 2),
        },
    }


# =====================================================================
# ESCANEO VECTORIZADO / CONCURRENTE DE MERCADO (CRITERIO EXPERTO CORTO PLAZO)
# =====================================================================

def evaluate_short_term_expert_structure(
    current_close: float,
    open_price: float,
    high_price: float,
    low_price: float,
    curr_ema20: float,
    ema20_slope_5d_pct: float,
    curr_sma50: float,
    curr_sma200: float,
    curr_adx: float,
    plus_di: float,
    minus_di: float,
    curr_k: float,
    curr_d: float,
    prev_k: float,
    prev_d: float,
    rsi_14: float,
    curr_atr: float,
    rvol_20: float,
    ret_1d_pct: float,
    ret_3d_pct: float,
    ret_5d_pct: float,
    params: dict[str, Any],
    er_10: float = 0.30,
    ret_20d_pct: float = 1.0,
) -> dict[str, Any]:
    """
    Motor de Evaluación Cuantitativa Experta para Corto Plazo (Swing Táctico 1-10 sesiones):
    1. Flujo Direccional Real (DMI +DI vs -DI) y Techo de Agotamiento Parabólico (ADX <= 35).
    2. Eficiencia Fractal de Kaufman (ER_10 >= 0.14) y Volatilidad Endógena (ATR% >= 1.80%).
    3. Anatomía de Vela (Price Action) y Escudo Anti-Cuchillo Cayendo (Anti-Falling Knife).
    4. Timing de Osciladores (Estocástico 14,4 + RSI 14) y Liquidez Institucional (RVOL 20).
    """
    adx_bull_min = float(params.get("adx_bullish_min", 19.0))
    adx_exhaust_max = float(params.get("adx_exhaustion_max", 35.0))
    adx_lat_max = float(params.get("adx_lateral_max", 20.0))
    stoch_bull_os = float(params.get("stoch_bullish_oversold", 36.0))
    stoch_lat_os = float(params.get("stoch_lateral_oversold", 20.0))
    allow_lateral = bool(params.get("enable_lateral_regime_trades", True))
    anti_knife_enabled = bool(params.get("anti_falling_knife_enabled", True))
    max_bearish_body_atr = float(params.get("max_bearish_body_atr_for_entry", 0.80))
    min_clv = float(params.get("min_close_location_value", 0.25))
    min_quant_score = int(params.get("min_quant_score", 55))
    min_atr_pct = float(params.get("min_atr_volatility_pct", 1.80))
    min_er10 = float(params.get("min_kaufman_efficiency_ratio", 0.14))
    min_rvol = float(params.get("min_relative_volume_rvol", 0.60))
    min_ret20 = float(params.get("min_momentum_20d_pct", -2.0))
    atr_pct = ((curr_atr / current_close) * 100.0) if current_close > 0 else 0.0

    candle_range = max(0.0001, high_price - low_price)
    close_location_value = (current_close - low_price) / candle_range  # 0.0 = mínimo del día, 1.0 = máximo
    candle_body = current_close - open_price
    candle_body_atr = (candle_body / curr_atr) if curr_atr > 0 else 0.0
    lower_wick = min(open_price, current_close) - low_price
    lower_wick_ratio = lower_wick / candle_range
    di_spread = plus_di - minus_di

    # Detección de "Cuchillo Cayendo" (Falling Knife) / Vela de Rotura Bajista de Corto Plazo
    severe_bearish_candle = (candle_body_atr <= -max_bearish_body_atr) or (
        ret_1d_pct <= -2.8 and close_location_value < min_clv
    )
    short_term_waterfall = (
        ret_3d_pct <= -4.2
        and current_close < curr_ema20
        and minus_di > plus_di
        and candle_body < 0
    )
    distribution_day = (
        candle_body_atr < -0.55 and rvol_20 >= 1.25 and close_location_value < 0.35
    )
    falling_knife = bool(
        anti_knife_enabled and (severe_bearish_candle or short_term_waterfall or distribution_day)
    )

    # Clasificación de Régimen en 5 estados reales de corto plazo
    above_sma200 = current_close > curr_sma200
    above_sma50 = current_close >= curr_sma50 * 0.995
    above_ema20 = current_close >= curr_ema20 * 0.997
    buyers_in_control = plus_di >= minus_di

    if above_sma200 and (
        falling_knife
        or (current_close < curr_ema20 and minus_di > plus_di + 3.0)
        or (current_close < curr_sma50 and ema20_slope_5d_pct < -0.35 and minus_di > plus_di)
    ):
        regime = "BEARISH_BREAKDOWN"
    elif not above_sma200 and (minus_di > plus_di or current_close < curr_ema20):
        regime = "BEARISH_TREND"
    elif (
        above_sma200
        and above_ema20
        and curr_ema20 >= curr_sma50 * 0.99
        and buyers_in_control
        and not falling_knife
        and (curr_adx >= adx_bull_min or ema20_slope_5d_pct > 0.20)
    ):
        regime = "BULLISH_TREND"
    elif (
        above_sma200
        and curr_sma50 >= curr_sma200 * 0.985
        and not falling_knife
        and minus_di <= plus_di + 5.0
        and (above_sma50 or close_location_value >= 0.40)
    ):
        regime = "BULLISH_PULLBACK"
    elif curr_adx < adx_lat_max and not falling_knife and abs(di_spread) < 12.0:
        regime = "LATERAL_RANGE"
    else:
        regime = "NEUTRAL"

    # Puntuación Cuantitativa Multi-Factor (0 - 100)
    score = 0
    criteria: list[dict[str, Any]] = []

    # Criterio 1: Estructura Multi-Media (0-25 pts)
    ma_pts = 0
    if above_sma200:
        ma_pts += 9
    if curr_sma50 >= curr_sma200 and above_sma50:
        ma_pts += 8
    if above_ema20 or ema20_slope_5d_pct >= 0.0:
        ma_pts += 8
    score += ma_pts
    criteria.append(
        {
            "label": "Estructura Multi-Media (EMA20 / SMA50 / SMA200)",
            "passed": ma_pts >= 16,
            "detail": (
                f"Precio ${current_close:.2f} vs EMA20 ${curr_ema20:.2f} "
                f"(pendiente 5d {ema20_slope_5d_pct:+.2f}%), SMA50 ${curr_sma50:.2f}, SMA200 ${curr_sma200:.2f}"
            ),
        }
    )

    # Criterio 2: Flujo Direccional DMI (+DI vs -DI) (0-20 pts)
    if di_spread >= 5.0 and curr_adx >= adx_bull_min:
        dmi_pts = 20
    elif di_spread >= 0.0:
        dmi_pts = 15
    elif di_spread >= -4.0:
        dmi_pts = 8
    else:
        dmi_pts = 0
    score += dmi_pts
    criteria.append(
        {
            "label": "Flujo Direccional DMI (+DI vs -DI)",
            "passed": di_spread >= -2.0,
            "detail": f"+DI {plus_di:.1f} vs -DI {minus_di:.1f} (Dif {di_spread:+.1f} · ADX {curr_adx:.1f})",
        }
    )

    # Criterio 3: Price Action & Anti-Falling Knife (0-25 pts)
    if falling_knife:
        pa_pts = -20
    else:
        pa_pts = 0
        if candle_body >= 0 or lower_wick_ratio >= 0.30:
            pa_pts += 12
        if close_location_value >= 0.45:
            pa_pts += 8
        elif close_location_value >= min_clv:
            pa_pts += 4
        if candle_body_atr > -0.50:
            pa_pts += 5
    score += pa_pts
    criteria.append(
        {
            "label": "Acción del Precio y Vela (Anti-Falling Knife)",
            "passed": not falling_knife and close_location_value >= min_clv,
            "detail": (
                f"Vela {ret_1d_pct:+.2f}% (Cuerpo {candle_body_atr:+.2f}xATR · Cierre en el "
                f"{close_location_value*100:.0f}% del rango diario)"
                + (" · ¡ALERTA CUCHILLO CAYENDO!" if falling_knife else " · Sin capitulación")
            ),
        }
    )

    # Criterio 4: Timing de Osciladores (Estocástico 14,4 + RSI 14) (0-20 pts)
    stoch_cross_up = prev_k <= prev_d and curr_k > curr_d
    stoch_rising_in_value = curr_k > curr_d and curr_k > prev_k and curr_k <= 40.0
    osc_pts = 0
    if stoch_cross_up and curr_k <= stoch_bull_os:
        osc_pts += 14
    elif stoch_cross_up or stoch_rising_in_value:
        osc_pts += 10
    elif curr_k <= stoch_bull_os:
        osc_pts += 6

    if 30.0 <= rsi_14 <= 60.0:
        osc_pts += 6
    elif 24.0 <= rsi_14 < 30.0:
        osc_pts += 4
    score += osc_pts
    criteria.append(
        {
            "label": "Timing Corto Plazo (Stoch 14,4 + RSI 14)",
            "passed": osc_pts >= 12,
            "detail": f"Stoch %K {curr_k:.1f} / %D {curr_d:.1f} · RSI(14) {rsi_14:.1f}",
        }
    )

    # Criterio 5: Volumen Relativo RVOL(20) (0-10 pts)
    if candle_body >= 0 and rvol_20 >= 0.95:
        vol_pts = 10
    elif not falling_knife and rvol_20 <= 1.15:
        vol_pts = 7
    else:
        vol_pts = 0
    score += vol_pts
    criteria.append(
        {
            "label": "Volumen Institucional (RVOL 20d)",
            "passed": vol_pts >= 7,
            "detail": f"RVOL {rvol_20:.2f}x media 20 sesiones",
        }
    )

    quant_score = max(0, min(100, int(round(score))))

    # Determinación de Sesgo de Corto Plazo (Largo vs Presión Corta)
    if regime in ("BEARISH_BREAKDOWN", "BEARISH_TREND") or falling_knife or di_spread < -5.0:
        short_term_bias = "SHORT_PRESSURE"
    elif regime in ("BULLISH_TREND", "BULLISH_PULLBACK") and quant_score >= 65:
        short_term_bias = "LONG_CONVICTION"
    elif regime in ("BULLISH_TREND", "BULLISH_PULLBACK", "LATERAL_RANGE") and quant_score >= 50:
        short_term_bias = "PULLBACK_WATCH"
    else:
        short_term_bias = "NEUTRAL_WAIT"

    # Regla de Disparo Estricta Econométrica (100% Agnóstica a la Compañía)
    signal_triggered = False
    block_reason: Optional[str] = None
    econometric_pass = (
        curr_adx >= adx_bull_min
        and curr_adx <= adx_exhaust_max
        and atr_pct >= min_atr_pct
        and er_10 >= min_er10
        and rvol_20 >= min_rvol
        and ret_20d_pct >= min_ret20
        and buyers_in_control
    )

    if falling_knife:
        block_reason = (
            f"Bloqueado por Escudo Anti-Cuchillo Cayendo: caída {ret_1d_pct:+.2f}% "
            f"(cuerpo {candle_body_atr:+.2f}xATR, CLV {close_location_value*100:.0f}%)."
        )
    elif regime in ("BEARISH_BREAKDOWN", "BEARISH_TREND"):
        block_reason = (
            f"Presión vendedora de corto plazo activa ({regime}: -DI {minus_di:.1f} > +DI {plus_di:.1f}, "
            f"precio bajo EMA20 ${curr_ema20:.2f})."
        )
    elif curr_adx > adx_exhaust_max:
        block_reason = (
            f"Filtro Estadístico de Agotamiento Climático: ADX {curr_adx:.1f} > {adx_exhaust_max:.1f} "
            "(riesgo elevado de reversión post-euforia)."
        )
    elif er_10 < min_er10:
        block_reason = (
            f"Filtro Fractal de Kaufman: ER(10)={er_10:.2f} < {min_er10:.2f} "
            "(ruido browniano errático de alta frecuencia)."
        )
    elif atr_pct < min_atr_pct:
        block_reason = (
            f"Filtro de Compresión de Volatilidad: ATR%={atr_pct:.2f}% < {min_atr_pct:.2f}% "
            "(inercia insuficiente para alcanzar +2R en ventana swing)."
        )
    elif regime in ("BULLISH_TREND", "BULLISH_PULLBACK"):
        tactical_dip_trigger = (
            (stoch_cross_up and curr_k <= stoch_bull_os)
            or (
                stoch_rising_in_value
                and 30.0 <= rsi_14 <= 58.0
                and close_location_value >= 0.45
                and (above_sma50 or above_ema20)
            )
        )
        if tactical_dip_trigger and econometric_pass and quant_score >= min_quant_score:
            signal_triggered = True
    elif regime == "LATERAL_RANGE" and allow_lateral:
        if (
            stoch_cross_up
            and curr_k <= stoch_lat_os
            and close_location_value >= 0.40
            and er_10 >= min_er10
            and atr_pct >= min_atr_pct
            and quant_score >= min_quant_score
        ):
            signal_triggered = True

    # Resumen diagnóstico ejecutivo de 1 frase
    if regime == "BEARISH_BREAKDOWN":
        expert_summary = (
            f"Rotura bajista corto plazo (-DI {minus_di:.1f} > +DI {plus_di:.1f}, "
            f"bajo EMA20 ${curr_ema20:.2f}, vela {ret_1d_pct:+.1f}%). Evitar compras."
        )
    elif regime == "BEARISH_TREND":
        expert_summary = (
            f"Tendencia bajista primaria y táctica (bajo SMA200 ${curr_sma200:.2f} y EMA20 ${curr_ema20:.2f}, "
            f"-DI {minus_di:.1f} dominante)."
        )
    elif regime == "BULLISH_TREND":
        expert_summary = (
            f"Tendencia alcista sana (+DI {plus_di:.1f} > -DI {minus_di:.1f}, sobre EMA20 ${curr_ema20:.2f} "
            f"y SMA50 ${curr_sma50:.2f}, Score {quant_score}/100)."
        )
    elif regime == "BULLISH_PULLBACK":
        expert_summary = (
            f"Pullback táctico controlado en estructura alcista (SMA50 ${curr_sma50:.2f} > SMA200 ${curr_sma200:.2f}, "
            f"CLV {close_location_value*100:.0f}%, Score {quant_score}/100)."
        )
    elif regime == "LATERAL_RANGE":
        expert_summary = (
            f"Rango lateral de baja direccionalidad (ADX {curr_adx:.1f} < {adx_lat_max:.0f}, "
            f"RSI {rsi_14:.1f}, Score {quant_score}/100)."
        )
    else:
        expert_summary = (
            f"Fase de transición sin ventaja táctica clara (Score {quant_score}/100 · "
            f"+DI {plus_di:.1f} / -DI {minus_di:.1f})."
        )

    return {
        "regime": regime,
        "short_term_bias": short_term_bias,
        "quant_score": quant_score,
        "falling_knife": falling_knife,
        "signal_triggered": signal_triggered,
        "block_reason": block_reason,
        "expert_summary": expert_summary,
        "expert_criteria": criteria,
        "close_location_value": round(close_location_value, 2),
        "candle_body_atr": round(candle_body_atr, 2),
        "di_spread": round(di_spread, 1),
    }


def analyze_single_ticker_df(
    ticker: str,
    df: pd.DataFrame,
    portfolio_value: float,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """
    Analiza el DataFrame OHLCV de un ticker con criterios expertos de corto plazo y devuelve:
    (opportunity_dict | None, ticker_snapshot_dict | None)
    """
    if df is None or df.empty:
        return None, None

    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        if ticker in df.columns.get_level_values(0):
            df = df[ticker]
        elif ticker in df.columns.get_level_values(-1):
            df.columns = df.columns.get_level_values(0)
        else:
            df.columns = df.columns.get_level_values(-1)

    df = df.dropna(subset=["Close", "High", "Low"])
    if len(df) < 55:
        return None, None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_s = df["Open"] if "Open" in df.columns else close
    vol_s = df["Volume"] if "Volume" in df.columns else pd.Series(0.0, index=df.index)

    stoch = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=4)
    k = stoch.stoch()
    d = stoch.stoch_signal()

    adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
    adx = adx_ind.adx()
    plus_di_s = adx_ind.adx_pos()
    minus_di_s = adx_ind.adx_neg()

    ema20 = EMAIndicator(close=close, window=20).ema_indicator()
    sma50 = SMAIndicator(close=close, window=50).sma_indicator()
    sma200 = SMAIndicator(close=close, window=200).sma_indicator()
    rsi14 = RSIIndicator(close=close, window=14).rsi()
    atr = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    vol_sma20 = vol_s.rolling(20).mean()

    current_close = float(close.iloc[-1])
    open_price = float(open_s.iloc[-1]) if not pd.isna(open_s.iloc[-1]) else current_close
    high_price = float(high.iloc[-1])
    low_price = float(low.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else current_close
    close_3d = float(close.iloc[-4]) if len(close) >= 4 else prev_close
    close_5d = float(close.iloc[-6]) if len(close) >= 6 else prev_close

    curr_k = float(k.iloc[-1])
    curr_d = float(d.iloc[-1])
    prev_k = float(k.iloc[-2])
    prev_d = float(d.iloc[-2])
    curr_adx = float(adx.iloc[-1])
    plus_di = float(plus_di_s.iloc[-1]) if not pd.isna(plus_di_s.iloc[-1]) else 20.0
    minus_di = float(minus_di_s.iloc[-1]) if not pd.isna(minus_di_s.iloc[-1]) else 20.0
    curr_atr = float(atr.iloc[-1])
    curr_ema20 = float(ema20.iloc[-1]) if not pd.isna(ema20.iloc[-1]) else current_close
    prev_ema20_5d = float(ema20.iloc[-6]) if len(ema20) >= 6 and not pd.isna(ema20.iloc[-6]) else curr_ema20
    ema20_slope_5d_pct = ((curr_ema20 - prev_ema20_5d) / prev_ema20_5d * 100.0) if prev_ema20_5d > 0 else 0.0
    curr_sma50 = float(sma50.iloc[-1]) if not pd.isna(sma50.iloc[-1]) else current_close
    curr_sma = float(sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else curr_sma50
    curr_rsi = float(rsi14.iloc[-1]) if not pd.isna(rsi14.iloc[-1]) else 50.0

    curr_vol = float(vol_s.iloc[-1]) if not pd.isna(vol_s.iloc[-1]) else 0.0
    avg_vol20 = float(vol_sma20.iloc[-1]) if not pd.isna(vol_sma20.iloc[-1]) and float(vol_sma20.iloc[-1]) > 0 else 0.0
    rvol_20 = (curr_vol / avg_vol20) if avg_vol20 > 0 else 1.0

    close_20d = float(close.iloc[-21]) if len(close) >= 21 else close_5d
    if len(close) >= 11:
        net_chg_10 = abs(float(close.iloc[-1]) - float(close.iloc[-11]))
        sum_chg_10 = float(close.diff(1).abs().iloc[-10:].sum())
        er_10 = (net_chg_10 / sum_chg_10) if sum_chg_10 > 0 else 0.0
    else:
        er_10 = 0.30

    ret_1d_pct = ((current_close - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
    ret_3d_pct = ((current_close - close_3d) / close_3d * 100.0) if close_3d > 0 else 0.0
    ret_5d_pct = ((current_close - close_5d) / close_5d * 100.0) if close_5d > 0 else 0.0
    ret_20d_pct = ((current_close - close_20d) / close_20d * 100.0) if close_20d > 0 else 0.0

    if any(pd.isna(v) for v in (current_close, curr_k, curr_d, prev_k, prev_d, curr_adx, curr_atr)):
        return None, None

    params = audit_agent.strategy_params
    base_atr_mult = float(params.get("atr_stop_loss_multiplier", 1.5))
    atr_pct = ((curr_atr / current_close) * 100.0) if current_close > 0 else 2.0

    # Multiplicador ATR Endógeno Continuo (100% agnóstico a la compañía o sector)
    if bool(params.get("endogenous_atr_scaling_enabled", True)):
        atr_sl_mult = min(2.0, max(1.5, base_atr_mult + 0.12 * (atr_pct - 1.8)))
    else:
        atr_sl_mult = base_atr_mult

    tp_rr = float(params.get("take_profit_reward_risk_ratio", 2.0))

    expert = evaluate_short_term_expert_structure(
        current_close=current_close,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        curr_ema20=curr_ema20,
        ema20_slope_5d_pct=ema20_slope_5d_pct,
        curr_sma50=curr_sma50,
        curr_sma200=curr_sma,
        curr_adx=curr_adx,
        plus_di=plus_di,
        minus_di=minus_di,
        curr_k=curr_k,
        curr_d=curr_d,
        prev_k=prev_k,
        prev_d=prev_d,
        rsi_14=curr_rsi,
        curr_atr=curr_atr,
        rvol_20=rvol_20,
        ret_1d_pct=ret_1d_pct,
        ret_3d_pct=ret_3d_pct,
        ret_5d_pct=ret_5d_pct,
        params=params,
        er_10=er_10,
        ret_20d_pct=ret_20d_pct,
    )

    regime = expert["regime"]
    signal_triggered = expert["signal_triggered"]

    stop_loss = round(current_close - (atr_sl_mult * curr_atr), 2)
    risk_distance = current_close - stop_loss
    take_profit = round(current_close + (tp_rr * risk_distance), 2)

    snapshot = {
        "ticker": ticker,
        "price": round(current_close, 2),
        "ema_20": round(curr_ema20, 2),
        "ema20_slope_pct": round(ema20_slope_5d_pct, 2),
        "sma_50": round(curr_sma50, 2),
        "sma_200": round(curr_sma, 2),
        "adx": round(curr_adx, 2),
        "plus_di": round(plus_di, 1),
        "minus_di": round(minus_di, 1),
        "di_spread": expert["di_spread"],
        "rsi_14": round(curr_rsi, 1),
        "stoch_k": round(curr_k, 2),
        "stoch_d": round(curr_d, 2),
        "atr_14": round(curr_atr, 2),
        "rvol_20": round(rvol_20, 2),
        "ret_1d_pct": round(ret_1d_pct, 2),
        "ret_5d_pct": round(ret_5d_pct, 2),
        "close_location_value": expert["close_location_value"],
        "candle_body_atr": expert["candle_body_atr"],
        "falling_knife": expert["falling_knife"],
        "short_term_bias": expert["short_term_bias"],
        "quant_score": expert["quant_score"],
        "expert_summary": expert["expert_summary"],
        "expert_criteria": expert["expert_criteria"],
        "market_regime": regime,
        "signal_triggered": signal_triggered,
        "provisional_stop_loss": stop_loss,
        "provisional_take_profit": take_profit,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if expert["block_reason"]:
        snapshot["rejection_reason"] = expert["block_reason"]

    if not signal_triggered:
        return None, snapshot

    validation = evaluate_risk_math(portfolio_value, current_close, stop_loss, take_profit)
    if not validation["viable"]:
        snapshot["rejection_reason"] = validation.get("reason")
        return None, snapshot

    opportunity = {
        "ticker": ticker,
        "market_regime": regime,
        "short_term_bias": expert["short_term_bias"],
        "quant_score": expert["quant_score"],
        "falling_knife": expert["falling_knife"],
        "expert_summary": expert["expert_summary"],
        "expert_criteria": expert["expert_criteria"],
        "entry_price": round(current_close, 2),
        "ema_20": round(curr_ema20, 2),
        "ema20_slope_pct": round(ema20_slope_5d_pct, 2),
        "sma_50": round(curr_sma50, 2),
        "sma_200": round(curr_sma, 2),
        "atr_14": round(curr_atr, 2),
        "rsi_14": round(curr_rsi, 1),
        "rvol_20": round(rvol_20, 2),
        "ret_1d_pct": round(ret_1d_pct, 2),
        "ret_5d_pct": round(ret_5d_pct, 2),
        "close_location_value": expert["close_location_value"],
        "candle_body_atr": expert["candle_body_atr"],
        "stoch_k": round(curr_k, 2),
        "stoch_d": round(curr_d, 2),
        "adx": round(curr_adx, 2),
        "plus_di": round(plus_di, 1),
        "minus_di": round(minus_di, 1),
        "di_spread": expert["di_spread"],
        "portfolio_value_used": round(portfolio_value, 2),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        **validation,
    }
    return opportunity, snapshot


from concurrent.futures import ThreadPoolExecutor, as_completed

def _scan_cluster_worker(
    cluster_id: str,
    cluster_tickers: list[str],
    portfolio_value: float,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Subagente especializado de clúster: descarga y analiza de forma aislada y concurrente
    su bloque de 15 activos en < 1 segundo, estampando el 'cluster_id' en cada snapshot.
    """
    clean = [t.strip().upper() for t in cluster_tickers if t.strip()]
    if not clean:
        return cluster_id, [], []

    opps: list[dict[str, Any]] = []
    snaps: list[dict[str, Any]] = []

    try:
        raw_data = yf.download(
            clean,
            period="1y",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
        )

        for ticker in clean:
            try:
                if len(clean) == 1:
                    df = raw_data
                else:
                    if ticker not in raw_data.columns.get_level_values(0):
                        continue
                    df = raw_data[ticker]

                opp, snap = analyze_single_ticker_df(ticker, df, portfolio_value)
                if snap is not None:
                    snap["cluster_id"] = cluster_id
                    snaps.append(snap)
                if opp is not None:
                    opp["cluster_id"] = cluster_id
                    opps.append(opp)
            except Exception as inner_err:
                logger.debug(f"[{cluster_id}] Error evaluando {ticker}: {inner_err}")
                continue

    except Exception as batch_err:
        logger.warning(f"[{cluster_id}] Fallo batch en subagente ({batch_err}), aplicando fallback secuencial...")
        for ticker in clean:
            try:
                df = yf.download(ticker, period="1y", interval="1d", progress=False)
                opp, snap = analyze_single_ticker_df(ticker, df, portfolio_value)
                if snap is not None:
                    snap["cluster_id"] = cluster_id
                    snaps.append(snap)
                if opp is not None:
                    opp["cluster_id"] = cluster_id
                    opps.append(opp)
            except Exception:
                continue

    return cluster_id, opps, snaps


# =====================================================================
# FILTRO DE RÉGIMEN MACRO E INTRADÍA (MACRO REGIME GATEKEEPER)
# =====================================================================

_MACRO_REGIME_STATE: dict[str, Any] = {
    "last_fetched_at": 0.0,
    "cached_benchmarks": {},
    "last_lockdown_triggered_at": 0.0,
    "last_regime": {
        "enabled": True,
        "status": "RISK_ON",
        "semaphore": "GREEN",
        "veto_active": False,
        "score_boost_required": 0,
        "reason": "Inicializando monitor macro autónomo (SPY / QQQ / VWAP)...",
        "spy": {
            "price": 0.0,
            "daily_change_pct": 0.0,
            "intraday_from_open_pct": 0.0,
            "effective_drop_pct": 0.0,
            "vwap": 0.0,
            "above_vwap": True,
        },
        "qqq": {
            "price": 0.0,
            "daily_change_pct": 0.0,
            "intraday_from_open_pct": 0.0,
            "effective_drop_pct": 0.0,
            "vwap": 0.0,
            "above_vwap": True,
        },
        "market_breadth": {
            "total_assets": 60,
            "advancing_assets": 30,
            "declining_assets": 30,
            "advancing_pct": 50.0,
            "falling_knives_count": 0,
        },
        "vetoed_opportunities_count": 0,
        "vetoed_tickers": [],
        "cooldown_remaining_minutes": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    },
}


def _fetch_benchmark_metrics_sync(ticker: str) -> dict[str, Any]:
    """Descarga y calcula métricas diarias e intradía (incluyendo VWAP) para un ETF rector (SPY o QQQ)."""
    default_metric = {
        "price": 0.0,
        "daily_change_pct": 0.0,
        "intraday_from_open_pct": 0.0,
        "effective_drop_pct": 0.0,
        "vwap": 0.0,
        "above_vwap": True,
    }
    try:
        df_d = yf.download(ticker, period="5d", interval="1d", progress=False)
        if isinstance(df_d.columns, pd.MultiIndex):
            df_d.columns = df_d.columns.get_level_values(0)
        df_d = df_d.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df_d) < 2:
            return default_metric

        curr_close = float(df_d["Close"].iloc[-1])
        prev_close = float(df_d["Close"].iloc[-2])
        curr_open = float(df_d["Open"].iloc[-1])
        curr_high = float(df_d["High"].iloc[-1])
        curr_low = float(df_d["Low"].iloc[-1])

        daily_chg = ((curr_close - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        intraday_chg = ((curr_close - curr_open) / curr_open * 100.0) if curr_open > 0 else 0.0
        effective_drop = min(daily_chg, intraday_chg)

        # Cálculo de VWAP intradía sobre velas de 15m (con fallback al precio típico de la sesión)
        vwap_val = (curr_high + curr_low + curr_close) / 3.0
        try:
            df_15m = yf.download(ticker, period="2d", interval="15m", progress=False)
            if isinstance(df_15m.columns, pd.MultiIndex):
                df_15m.columns = df_15m.columns.get_level_values(0)
            df_15m = df_15m.dropna(subset=["High", "Low", "Close", "Volume"])
            if len(df_15m) > 0:
                last_day = df_15m.index[-1].date()
                day_bars = df_15m[df_15m.index.date == last_day]
                if len(day_bars) > 0:
                    typ = (day_bars["High"] + day_bars["Low"] + day_bars["Close"]) / 3.0
                    vol_sum = float(day_bars["Volume"].sum())
                    if vol_sum > 0:
                        vwap_val = float((typ * day_bars["Volume"]).sum() / vol_sum)
        except Exception:
            pass

        return {
            "price": round(curr_close, 2),
            "daily_change_pct": round(daily_chg, 2),
            "intraday_from_open_pct": round(intraday_chg, 2),
            "effective_drop_pct": round(effective_drop, 2),
            "vwap": round(vwap_val, 2),
            "above_vwap": bool(curr_close >= vwap_val * 0.999),
        }
    except Exception as exc:
        logger.debug(f"Error consultando benchmark macro {ticker}: {exc}")
        return default_metric


def evaluate_macro_regime_sync(
    snapshots: Optional[list[dict[str, Any]]] = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Evalúa de forma 100% autónoma y en tiempo real el Régimen Macro de Mercado (Macro Regime Gatekeeper):
    - Monitoriza SPY y QQQ (variación diaria, desde apertura y posición respecto al VWAP intradía).
    - Monitoriza la Amplitud Interna del Universo de 60 activos (% alcistas y nº de cuchillos cayendo).
    - Clasifica en 3 estados institucionales:
      1. GREEN / RISK_ON: Operativa normal en los 4 clústeres.
      2. YELLOW / CAUTION: Exigencia técnica reforzada (+8 pts en Quant Score).
      3. RED / RISK_OFF_LOCKDOWN: Veto automático de nuevas compras largas para blindar el capital.
    """
    params = audit_agent.strategy_params
    enabled = bool(params.get("macro_regime_gatekeeper_enabled", True))
    spy_thresh = float(params.get("macro_index_threshold_drop_pct", -0.75))
    qqq_thresh = float(params.get("macro_qqq_threshold_drop_pct", -1.00))
    caution_thresh = float(params.get("macro_caution_threshold_drop_pct", -0.40))
    vwap_filter = bool(params.get("macro_intraday_vwap_filter", True))
    min_breadth_ratio = float(params.get("macro_market_breadth_min_advancing_pct", 0.38))
    cooldown_mins = int(params.get("macro_cooldown_minutes_after_reversal", 30))

    now_mono = time.monotonic()
    if (
        force_refresh
        or not _MACRO_REGIME_STATE["cached_benchmarks"]
        or (now_mono - _MACRO_REGIME_STATE["last_fetched_at"]) >= 20.0
    ):
        spy_metrics = _fetch_benchmark_metrics_sync("SPY")
        qqq_metrics = _fetch_benchmark_metrics_sync("QQQ")
        if spy_metrics["price"] > 0 or qqq_metrics["price"] > 0:
            _MACRO_REGIME_STATE["cached_benchmarks"] = {"SPY": spy_metrics, "QQQ": qqq_metrics}
            _MACRO_REGIME_STATE["last_fetched_at"] = now_mono

    benchmarks = _MACRO_REGIME_STATE["cached_benchmarks"]
    spy = benchmarks.get("SPY") or _MACRO_REGIME_STATE["last_regime"]["spy"]
    qqq = benchmarks.get("QQQ") or _MACRO_REGIME_STATE["last_regime"]["qqq"]

    # Amplitud interna del universo de 60 tickers
    snaps_list = snapshots if snapshots is not None else scanner_worker.market_watch
    if snaps_list:
        total_assets = len(snaps_list)
        advancing_assets = sum(1 for s in snaps_list if float(s.get("ret_1d_pct", 0.0)) > 0.0)
        declining_assets = total_assets - advancing_assets
        falling_knives = sum(1 for s in snaps_list if bool(s.get("falling_knife", False)))
        advancing_ratio = advancing_assets / total_assets if total_assets > 0 else 0.50
    else:
        prev_b = _MACRO_REGIME_STATE["last_regime"]["market_breadth"]
        total_assets = prev_b["total_assets"]
        advancing_assets = prev_b["advancing_assets"]
        declining_assets = prev_b["declining_assets"]
        falling_knives = prev_b["falling_knives_count"]
        advancing_ratio = prev_b["advancing_pct"] / 100.0

    breadth_info = {
        "total_assets": total_assets,
        "advancing_assets": advancing_assets,
        "declining_assets": declining_assets,
        "advancing_pct": round(advancing_ratio * 100.0, 1),
        "falling_knives_count": falling_knives,
    }

    # Evaluación de disparadores de liquidación institucional (evita falsos positivos en días de gap alcista o martillos de acumulación)
    spy_liquidation = (
        (spy["daily_change_pct"] <= spy_thresh and spy["intraday_from_open_pct"] <= -0.30 and not spy["above_vwap"])
        or (spy["intraday_from_open_pct"] <= (spy_thresh * 1.5) and spy["daily_change_pct"] < 0.0 and not spy["above_vwap"])
    )
    qqq_liquidation = (
        (qqq["daily_change_pct"] <= qqq_thresh and qqq["intraday_from_open_pct"] <= -0.40 and not qqq["above_vwap"])
        or (qqq["intraday_from_open_pct"] <= (qqq_thresh * 1.5) and qqq["daily_change_pct"] < 0.0 and not qqq["above_vwap"])
    )
    vwap_breadth_breach = (
        vwap_filter
        and (not spy["above_vwap"])
        and (not qqq["above_vwap"])
        and (spy["daily_change_pct"] <= -0.50)
        and (advancing_ratio < min_breadth_ratio)
    )

    spy_breach = spy_liquidation
    qqq_breach = qqq_liquidation

    cooldown_rem_mins = 0
    if enabled and (spy_breach or qqq_breach or vwap_breadth_breach):
        _MACRO_REGIME_STATE["last_lockdown_triggered_at"] = now_mono
        status = "RISK_OFF_LOCKDOWN"
        semaphore = "RED"
        veto_active = True
        score_boost = 15
        triggers = []
        if spy_breach:
            triggers.append(f"SPY {spy['effective_drop_pct']:+.2f}% (límite {spy_thresh:+.2f}%)")
        if qqq_breach:
            triggers.append(f"QQQ {qqq['effective_drop_pct']:+.2f}% (límite {qqq_thresh:+.2f}%)")
        if vwap_breadth_breach:
            triggers.append(f"Rotura VWAP + Amplitud débil ({breadth_info['advancing_pct']:.1f}%)")
        reason = f"VETO MACRO ACTIVO: Liquidación detectada [{', '.join(triggers)}]. Compras bloqueadas en los 4 clústeres."
    elif (
        enabled
        and _MACRO_REGIME_STATE["last_lockdown_triggered_at"] > 0
        and (now_mono - _MACRO_REGIME_STATE["last_lockdown_triggered_at"]) < (cooldown_mins * 60.0)
    ):
        elapsed = now_mono - _MACRO_REGIME_STATE["last_lockdown_triggered_at"]
        cooldown_rem_mins = max(1, math.ceil(((cooldown_mins * 60.0) - elapsed) / 60.0))
        status = "RISK_OFF_LOCKDOWN"
        semaphore = "RED"
        veto_active = True
        score_boost = 15
        reason = (
            f"ENFRIAMIENTO POST-LIQUIDACIÓN ({cooldown_rem_mins}m restantes): "
            f"Esperando consolidación sobre VWAP para evitar Bull Traps."
        )
    elif enabled and (
        spy["effective_drop_pct"] <= caution_thresh
        or qqq["effective_drop_pct"] <= (caution_thresh * 1.5)
        or advancing_ratio < 0.45
    ):
        status = "CAUTION"
        semaphore = "YELLOW"
        veto_active = False
        score_boost = 8
        reason = (
            f"PRECAUCIÓN MACRO: Presión correctiva moderada (SPY {spy['daily_change_pct']:+.2f}%, "
            f"QQQ {qqq['daily_change_pct']:+.2f}%, Amplitud {breadth_info['advancing_pct']:.1f}%). "
            f"Exigencia Quant Score elevada +8 pts."
        )
    else:
        status = "RISK_ON"
        semaphore = "GREEN"
        veto_active = False
        score_boost = 0
        reason = (
            f"RÉGIMEN SALUDABLE: SPY {spy['daily_change_pct']:+.2f}% | "
            f"QQQ {qqq['daily_change_pct']:+.2f}% | "
            f"Amplitud {breadth_info['advancing_pct']:.1f}% alcista."
        )

    regime_payload = {
        "enabled": enabled,
        "status": status,
        "semaphore": semaphore,
        "veto_active": veto_active,
        "score_boost_required": score_boost,
        "reason": reason,
        "spy": spy,
        "qqq": qqq,
        "market_breadth": breadth_info,
        "thresholds": {
            "spy_drop_pct": spy_thresh,
            "qqq_drop_pct": qqq_thresh,
            "caution_drop_pct": caution_thresh,
            "min_breadth_pct": round(min_breadth_ratio * 100.0, 1),
            "cooldown_minutes": cooldown_mins,
        },
        "vetoed_opportunities_count": _MACRO_REGIME_STATE["last_regime"].get("vetoed_opportunities_count", 0),
        "vetoed_tickers": _MACRO_REGIME_STATE["last_regime"].get("vetoed_tickers", []),
        "cooldown_remaining_minutes": cooldown_rem_mins,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _MACRO_REGIME_STATE["last_regime"] = regime_payload
    return regime_payload


def run_market_scan_sync(
    tickers: list[str],
    portfolio_value: float,
    force_test_signal: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Ejecuta el barrido de mercado particionando el universo en Clústeres (Subagentes concurrentes)
    y aplica de forma automática el Filtro de Régimen Macro (Macro Regime Gatekeeper).
    """
    clean_tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not clean_tickers:
        return [], []

    configured_clusters: dict[str, list[str]] = (
        audit_agent.strategy_params.get("clusters") or DEFAULT_CLUSTERS
    )

    # Si se especificó una lista personalizada de tickers, agrupamos según clúster o en bloques de 15
    active_clusters: dict[str, list[str]] = {}
    remaining_tickers = list(clean_tickers)

    for c_id, c_tickers in configured_clusters.items():
        matched = [t for t in remaining_tickers if t in c_tickers]
        if matched:
            active_clusters[c_id] = matched
            remaining_tickers = [t for t in remaining_tickers if t not in matched]

    # Cualquier ticker restante se agrupa en bloques de 15 bajo subagentes dinámicos
    if remaining_tickers:
        for idx in range(0, len(remaining_tickers), 15):
            chunk_id = f"CLUSTER_CUSTOM_{idx // 15 + 1}"
            active_clusters[chunk_id] = remaining_tickers[idx : idx + 15]

    opportunities: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []

    # Ejecución paralela de los Subagentes de Clúster
    max_workers = min(len(active_clusters), 8) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_scan_cluster_worker, c_id, c_list, portfolio_value): c_id
            for c_id, c_list in active_clusters.items()
        }
        for future in as_completed(futures):
            c_id = futures[future]
            try:
                _, c_opps, c_snaps = future.result()
                opportunities.extend(c_opps)
                snapshots.extend(c_snaps)
            except Exception as exc:
                logger.error(f"Error en subagente {c_id}: {exc}")

    # Ordenar radar y oportunidades consolidadas por calidad cuantitativa (quant_score descendente)
    snapshots.sort(key=lambda s: int(s.get("quant_score", 0)), reverse=True)
    opportunities.sort(key=lambda o: int(o.get("quant_score", 0)), reverse=True)

    # Evaluación Autónoma del Régimen Macro e Intradía (SPY / QQQ / VWAP / Breadth)
    macro_regime = evaluate_macro_regime_sync(snapshots=snapshots)
    vetoed_tickers: list[str] = []

    if macro_regime["veto_active"] and not force_test_signal:
        vetoed_tickers = [o["ticker"] for o in opportunities]
        snap_by_ticker = {s["ticker"]: s for s in snapshots}
        for opp in opportunities:
            tk = opp["ticker"]
            if tk in snap_by_ticker:
                snap_by_ticker[tk]["signal_triggered"] = False
                snap_by_ticker[tk]["macro_vetoed"] = True
                snap_by_ticker[tk]["rejection_reason"] = f"[MACRO GATEKEEPER] {macro_regime['reason']}"
        if vetoed_tickers:
            logger.warning(
                f"[MACRO GATEKEEPER] {macro_regime['status']} -> Suprimidas {len(vetoed_tickers)} "
                f"señales de compra ({', '.join(vetoed_tickers)}) para blindar el capital."
            )
        opportunities = []
    elif macro_regime["status"] == "CAUTION" and not force_test_signal and opportunities:
        base_min_score = int(audit_agent.strategy_params.get("min_quant_score", 55))
        caution_min_score = base_min_score + int(macro_regime.get("score_boost_required", 8))
        filtered_opps: list[dict[str, Any]] = []
        snap_by_ticker = {s["ticker"]: s for s in snapshots}
        for opp in opportunities:
            if int(opp.get("quant_score", 0)) >= caution_min_score:
                filtered_opps.append(opp)
            else:
                tk = opp["ticker"]
                vetoed_tickers.append(tk)
                if tk in snap_by_ticker:
                    snap_by_ticker[tk]["signal_triggered"] = False
                    snap_by_ticker[tk]["macro_vetoed"] = True
                    snap_by_ticker[tk]["rejection_reason"] = (
                        f"[PRECAUCIÓN MACRO] Score {opp.get('quant_score')} < {caution_min_score} exigido en mercado correctivo."
                    )
        opportunities = filtered_opps

    macro_regime["vetoed_opportunities_count"] = len(vetoed_tickers)
    macro_regime["vetoed_tickers"] = vetoed_tickers
    _MACRO_REGIME_STATE["last_regime"] = macro_regime

    # MODO PRUEBA OPCIONAL (PoC): elige el activo con mayor puntuación técnica sin cuchillo cayendo
    if force_test_signal and len(opportunities) == 0 and snapshots:
        healthy_candidates = [
            s
            for s in snapshots
            if not s.get("falling_knife")
            and s.get("market_regime") not in ("BEARISH_BREAKDOWN", "BEARISH_TREND")
        ]
        best_snap = healthy_candidates[0] if healthy_candidates else snapshots[0]
        sample_ticker = best_snap["ticker"]
        test_entry = float(best_snap["price"])
        test_atr = float(best_snap["atr_14"])
        test_stop = float(best_snap["provisional_stop_loss"])
        test_tp = float(best_snap["provisional_take_profit"])
        validation = evaluate_risk_math(portfolio_value, test_entry, test_stop, test_tp)
        if validation["viable"]:
            opportunities.append(
                {
                    "ticker": sample_ticker,
                    "cluster_id": best_snap.get("cluster_id", "TECH_GROWTH"),
                    "market_regime": f"{best_snap['market_regime']} (SIMULACIÓN POC)",
                    "short_term_bias": best_snap.get("short_term_bias", "LONG_CONVICTION"),
                    "quant_score": best_snap.get("quant_score", 75),
                    "falling_knife": best_snap.get("falling_knife", False),
                    "expert_summary": best_snap.get("expert_summary", ""),
                    "expert_criteria": best_snap.get("expert_criteria", []),
                    "entry_price": test_entry,
                    "ema_20": best_snap.get("ema_20", test_entry),
                    "ema20_slope_pct": best_snap.get("ema20_slope_pct", 0.0),
                    "sma_50": best_snap.get("sma_50", test_entry),
                    "sma_200": best_snap.get("sma_200", test_entry),
                    "atr_14": test_atr,
                    "rsi_14": best_snap.get("rsi_14", 48.0),
                    "rvol_20": best_snap.get("rvol_20", 1.0),
                    "ret_1d_pct": best_snap.get("ret_1d_pct", 0.0),
                    "ret_5d_pct": best_snap.get("ret_5d_pct", 0.0),
                    "close_location_value": best_snap.get("close_location_value", 0.6),
                    "candle_body_atr": best_snap.get("candle_body_atr", 0.2),
                    "stoch_k": best_snap.get("stoch_k", 22.0),
                    "stoch_d": best_snap.get("stoch_d", 18.0),
                    "adx": best_snap.get("adx", 26.0),
                    "plus_di": best_snap.get("plus_di", 26.0),
                    "minus_di": best_snap.get("minus_di", 16.0),
                    "di_spread": best_snap.get("di_spread", 10.0),
                    "portfolio_value_used": round(portfolio_value, 2),
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    **validation,
                }
            )

    for opp in opportunities:
        opp["macro_regime_status"] = macro_regime["status"]
        opp["ai_thesis"] = build_ai_thesis(opp)

    return opportunities, snapshots


def build_ai_thesis(opp: dict[str, Any]) -> dict[str, Any]:
    """
    Tesis ejecutiva de 3 líneas generada de forma determinista (0 ms) con telemetría de corto plazo.
    """
    entry = float(opp.get("entry_price", 0.0) or 0.0)
    ema20 = float(opp.get("ema_20") or entry)
    sma50 = float(opp.get("sma_50") or entry)
    sma200 = float(opp.get("sma_200") or 0.0)
    pct_vs_sma = ((entry - sma200) / sma200 * 100.0) if sma200 > 0 else 0.0
    score = opp.get("quant_score", 0)
    plus_di = float(opp.get("plus_di") or 0.0)
    minus_di = float(opp.get("minus_di") or 0.0)
    k = float(opp.get("stoch_k") or 0.0)
    d = float(opp.get("stoch_d") or 0.0)
    rsi = float(opp.get("rsi_14") or 50.0)
    clv = float(opp.get("close_location_value") or 0.5) * 100.0
    metrics = opp.get("financial_metrics", {}) or {}

    line1 = opp.get("expert_summary") or (
        f"Score Cuantitativo {score}/100 ({pct_vs_sma:+.1f}% vs SMA200, EMA20 ${ema20:.2f}, SMA50 ${sma50:.2f})."
    )
    line2 = (
        f"Flujo DMI (+DI {plus_di:.1f} vs -DI {minus_di:.1f}) · Stoch %K {k:.1f} / %D {d:.1f} · "
        f"RSI {rsi:.1f} · Cierre diario en el {clv:.0f}% del rango."
    )
    line3 = (
        f"Riesgo {metrics.get('portfolio_risk_pct', 0)}% "
        f"(-${metrics.get('max_loss_with_commissions', 0)}) con salida OCO 1:2 "
        f"(+${metrics.get('expected_net_profit', 0)} netos, comisiones "
        f"{metrics.get('commission_to_gross_profit_pct', 0)}% del beneficio)."
    )
    return {"source": "DETERMINISTIC_QUANT", "lines": [line1, line2, line3]}


def fetch_latest_price_and_atr_sync(ticker: str) -> Optional[dict[str, float]]:
    """Obtiene el último precio de mercado y ATR(14) en el instante exacto de aprobar."""
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False)
        if df.empty or len(df) < 15:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close", "High", "Low"])
        atr = AverageTrueRange(
            high=df["High"], low=df["Low"], close=df["Close"], window=14
        ).average_true_range()
        return {
            "price": round(float(df["Close"].iloc[-1]), 2),
            "atr": round(float(atr.iloc[-1]), 4),
        }
    except Exception:
        return None


# =====================================================================
# EJECUCIÓN ATÓMICA BRACKET OCO EN ALPACA PAPER
# =====================================================================

SIMULATED_ORDERS_HISTORY: list[dict[str, Any]] = []


def submit_bracket_order_sync(order: ExecuteOrderRequest) -> dict[str, Any]:
    """
    Envía una Orden Bracket (Entrada Manual + Salida Automática OCO Stop Loss & Take Profit)
    ajustada a condiciones reales de mercado:
    - 'marketable_limit' añade un pequeño colchón de slippage máximo (ej. 0.15%) al precio límite
      para garantizar ejecución inmediata sin quedarse fuera del movimiento, protegiendo el 1% de riesgo.
    - 'limit' usa el precio límite exacto.
    - 'market' lanza orden a mercado con salidas OCO ancladas.
    """
    client, is_configured, cfg = get_alpaca_trading_client()
    slippage_pct = (
        order.slippage_buffer_pct
        if order.slippage_buffer_pct is not None
        else cfg["default_slippage_pct"]
    )

    if order.order_type == "marketable_limit":
        effective_limit_price = round(order.entry_limit_price * (1.0 + slippage_pct), 2)
        # Aseguramos que el límite marketable nunca supere ni se acerque peligrosamente al Take Profit
        if effective_limit_price >= order.take_profit_price:
            effective_limit_price = round(order.entry_limit_price, 2)
    else:
        effective_limit_price = round(order.entry_limit_price, 2)

    stop_loss_price = round(order.stop_loss_price, 2)
    # En Alpaca, para órdenes Bracket se recomienda definir stop_price y opcionalmente stop_limit
    take_profit_price = round(order.take_profit_price, 2)

    bracket_payload = {
        "symbol": order.ticker.upper(),
        "qty": order.shares,
        "side": "buy",
        "type": "market" if order.order_type == "market" else "limit",
        "order_mode_applied": order.order_type,
        "time_in_force": order.time_in_force,
        "reference_entry_price": round(order.entry_limit_price, 2),
        "limit_price": None if order.order_type == "market" else effective_limit_price,
        "order_class": "bracket",
        "stop_loss": {"stop_price": stop_loss_price},
        "take_profit": {"limit_price": take_profit_price},
    }

    if not is_configured or client is None:
        sim_order_id = f"sim-oco-{uuid.uuid4().hex[:10]}"
        sim_record = {
            "order_id": sim_order_id,
            "status": "SIMULATED_BRACKET_SENT",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "message": (
                f"Simulación OK: Orden Bracket ({order.order_type.upper()}) para {order.shares} acciones "
                f"de {order.ticker.upper()} enviada. Salidas OCO automáticas activadas "
                f"(SL: ${stop_loss_price} | TP: ${take_profit_price})."
            ),
            "broker_order_structure": bracket_payload,
        }
        SIMULATED_ORDERS_HISTORY.insert(0, sim_record)
        return sim_record

    try:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        tif = TimeInForce.GTC if order.time_in_force == "gtc" else TimeInForce.DAY
        tp_req = TakeProfitRequest(limit_price=take_profit_price)
        sl_req = StopLossRequest(stop_price=stop_loss_price)

        if order.order_type == "market":
            alpaca_order = MarketOrderRequest(
                symbol=order.ticker.upper(),
                qty=order.shares,
                side=OrderSide.BUY,
                time_in_force=tif,
                order_class=OrderClass.BRACKET,
                take_profit=tp_req,
                stop_loss=sl_req,
            )
        else:
            alpaca_order = LimitOrderRequest(
                symbol=order.ticker.upper(),
                qty=order.shares,
                side=OrderSide.BUY,
                time_in_force=tif,
                limit_price=effective_limit_price,
                order_class=OrderClass.BRACKET,
                take_profit=tp_req,
                stop_loss=sl_req,
            )

        submitted = client.submit_order(order_data=alpaca_order)
        return {
            "status": "LIVE_PAPER_BRACKET_SENT",
            "order_id": str(submitted.id),
            "client_order_id": str(getattr(submitted, "client_order_id", "")),
            "alpaca_status": str(
                submitted.status.value if hasattr(submitted.status, "value") else submitted.status
            ),
            "created_at": str(getattr(submitted, "created_at", datetime.now(timezone.utc).isoformat())),
            "message": (
                f"Orden Bracket OCO enviada en vivo a Alpaca Paper ({order.shares}x {order.ticker.upper()}). "
                f"Salida automática armada en servidor: SL ${stop_loss_price} / TP ${take_profit_price}."
            ),
            "broker_order_structure": bracket_payload,
        }
    except Exception as exc:
        logger.error(f"Error enviando orden Bracket a Alpaca: {exc}")
        return {"status": "ERROR", "detail": str(exc), "attempted_payload": bracket_payload}


# =====================================================================
# WORKER AUTÓNOMO DE ESCANEO CONTINUO (15 SEGUNDOS START/STOP)
# =====================================================================

class AutonomousScannerWorker:
    def __init__(self) -> None:
        cfg = reload_env_config()
        self.running: bool = False
        self.interval_seconds: int = cfg["default_interval_seconds"]
        self.tickers: list[str] = cfg["default_tickers"]
        self.portfolio_value: float = cfg["default_portfolio_value"]
        self.portfolio_source: str = "CONFIG_DEFAULT"
        self.auto_sync_alpaca_equity: bool = True
        self.alpaca_baseline_equity: Optional[float] = None
        self.force_test_signal: bool = False

        # Configuración Modo Manual vs Full Automático + Cortafuegos 4%
        self.execution_mode: Literal["MANUAL", "FULL_AUTO"] = "MANUAL"
        self.max_session_drawdown_pct: float = 0.04
        self.auto_apply_audit_fixes: bool = True
        self.last_audit_report: Optional[dict[str, Any]] = None
        self.auto_executed_tickers_cooldown: dict[str, float] = {}
        self.auto_execution_cooldown_seconds: int = 900

        self.session_duration_minutes: Optional[int] = None
        self.session_ends_at: Optional[str] = None
        self._session_end_monotonic: Optional[float] = None
        self.last_stop_reason: Optional[str] = None

        self.scan_count: int = 0
        self.is_scanning_now: bool = False
        self.started_at: Optional[str] = None
        self.last_scan_started_at: Optional[str] = None
        self.last_scan_completed_at: Optional[str] = None
        self.last_scan_duration_ms: Optional[float] = None
        self.last_error: Optional[str] = None

        # Diccionario de oportunidades vivas indexadas por ticker para acceso O(1) al aprobar
        self.active_opportunities: dict[str, dict[str, Any]] = {}
        # Telemetría técnica en vivo de todos los tickers escaneados en el último ciclo
        self.market_watch: list[dict[str, Any]] = []
        if MARKET_WATCH_CACHE_FILE.exists():
            try:
                with open(MARKET_WATCH_CACHE_FILE, "r", encoding="utf-8") as f:
                    cached_snaps = json.load(f)
                    if isinstance(cached_snaps, list) and cached_snaps:
                        self.market_watch = cached_snaps
                        logger.info(f"Cargados {len(self.market_watch)} activos en el radar desde caché.")
            except Exception as e:
                logger.debug(f"No se pudo cargar market_watch_latest.json: {e}")
        # Control de cooldown de notificaciones webhook para no spamear el móvil cada 15s
        self.last_webhook_notified_at: dict[str, float] = {}
        self.webhook_cooldown_seconds: int = 300
        # Historial reciente de aprobaciones/ejecuciones manuales o automáticas
        self.execution_history: list[dict[str, Any]] = []

        self._task: Optional[asyncio.Task] = None
        self._stop_event: asyncio.Event = asyncio.Event()

        # Asesor IA (Gemini): caché por ticker (evita repetir llamadas cada 15 s) y peticiones en vuelo
        self.ai_cache_ttl_seconds: int = 600
        self.ai_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.ai_pending: set[str] = set()
        self._ai_tasks: set[asyncio.Task] = set()

    def _cached_advice(self, ticker: str) -> Optional[dict[str, Any]]:
        hit = self.ai_cache.get(ticker)
        if hit and (time.monotonic() - hit[0]) < self.ai_cache_ttl_seconds:
            return hit[1]
        return None

    async def request_advice(
        self, opp: dict[str, Any], timeout_seconds: Optional[float] = None, force: bool = False
    ) -> dict[str, Any]:
        """Obtiene el dictamen (de caché o de Gemini). Nunca lanza excepción."""
        ticker = opp["ticker"]
        if not force:
            cached = self._cached_advice(ticker)
            if cached:
                return cached
        advice = await ai_advisor.analyze_opportunity(
            opp, float(opp.get("portfolio_value_used", self.portfolio_value)), timeout_seconds
        )
        advice["requested_at"] = datetime.now(timezone.utc).isoformat()
        if advice.get("status") == "DONE":
            self.ai_cache[ticker] = (time.monotonic(), advice)
        return advice

    def schedule_background_advice(self, ticker: str) -> None:
        """Lanza la consulta a Gemini sin bloquear el escaneo ni la aprobación en 1 clic."""
        if ticker in self.ai_pending:
            return
        self.ai_pending.add(ticker)

        async def _job() -> None:
            try:
                current = self.active_opportunities.get(ticker)
                if current is None:
                    return
                advice = await self.request_advice(current)
                live = self.active_opportunities.get(ticker)
                if live is not None:
                    live["ai_advice"] = advice
            finally:
                self.ai_pending.discard(ticker)

        task = asyncio.create_task(_job())
        self._ai_tasks.add(task)
        task.add_done_callback(self._ai_tasks.discard)


    def _sync_effective_portfolio(self, acct_info: dict[str, Any], reset_baseline: bool = False) -> None:
        if acct_info.get("connected") and acct_info.get("equity", 0) > 0:
            live_eq = float(acct_info["equity"])
            if reset_baseline or self.alpaca_baseline_equity is None:
                self.alpaca_baseline_equity = live_eq
            virtual_base = audit_agent.strategy_params.get("virtual_portfolio_base")
            if virtual_base is not None and float(virtual_base) > 0:
                realized_and_unrealized_pnl = live_eq - self.alpaca_baseline_equity
                self.portfolio_value = round(float(virtual_base) + realized_and_unrealized_pnl, 2)
                self.portfolio_source = "VIRTUAL_POC_1000_ON_ALPACA"
            else:
                self.portfolio_value = round(live_eq, 2)
                self.portfolio_source = "ALPACA_LIVE_EQUITY"
        elif self.portfolio_source != "MANUAL_OVERRIDE":
            cfg = reload_env_config()
            self.portfolio_value = cfg["default_portfolio_value"]
            self.portfolio_source = "SIMULATION_FALLBACK"

    def _apply_session_timer(self, duration_minutes: Optional[int]) -> None:
        self.session_duration_minutes = duration_minutes
        if duration_minutes is not None and duration_minutes > 0:
            self._session_end_monotonic = time.monotonic() + (duration_minutes * 60.0)
            self.session_ends_at = (
                datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
            ).isoformat()
        else:
            self._session_end_monotonic = None
            self.session_ends_at = None

    async def start(self, req: Optional[ScannerStartRequest] = None) -> dict[str, Any]:
        duration_mins: Optional[int] = None
        if req is not None:
            if req.tickers:
                self.tickers = [t.strip().upper() for t in req.tickers if t.strip()]
            self.interval_seconds = req.interval_seconds
            self.execution_mode = req.execution_mode
            self.max_session_drawdown_pct = req.max_session_drawdown_pct
            self.auto_apply_audit_fixes = req.auto_apply_audit_fixes
            duration_mins = req.session_duration_minutes
            if req.portfolio_value is not None:
                self.portfolio_value = req.portfolio_value
                self.portfolio_source = "MANUAL_OVERRIDE"
            self.auto_sync_alpaca_equity = req.auto_sync_alpaca_equity
            self.force_test_signal = req.force_test_signal

        # Sincronizar equity inicial real antes de fijar el suelo del 4%
        if self.auto_sync_alpaca_equity and (req is None or req.portfolio_value is None):
            acct_info = await asyncio.to_thread(fetch_alpaca_account_sync)
            self._sync_effective_portfolio(acct_info, reset_baseline=True)

        self._apply_session_timer(duration_mins)
        self.last_stop_reason = None
        self.auto_executed_tickers_cooldown.clear()

        if self.running and self._task and not self._task.done():
            return {
                "message": "El worker de escaneo ya estaba en ejecución; parámetros, modo y temporizador actualizados en caliente.",
                "status": self.get_status(),
            }

        # Registrar inicio de sesión en el Agente Auditor Independiente (fija el suelo del 4%)
        audit_agent.start_session(
            execution_mode=self.execution_mode,
            initial_equity=self.portfolio_value,
            max_drawdown_pct=self.max_session_drawdown_pct,
            interval_seconds=self.interval_seconds,
            session_duration_minutes=self.session_duration_minutes,
            tickers=self.tickers,
        )

        self._stop_event.clear()
        self.running = True
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._task = asyncio.create_task(self._run_loop())
        mode_label = (
            f"modo={self.execution_mode}, duración={self.session_duration_minutes}m, stop_cartera=-{round(self.max_session_drawdown_pct*100, 1)}%"
            if self.session_duration_minutes
            else f"modo={self.execution_mode}, libre, stop_cartera=-{round(self.max_session_drawdown_pct*100, 1)}%"
        )
        logger.info(
            f"Scanner autónomo INICIADO (intervalo={self.interval_seconds}s, {mode_label}, tickers={len(self.tickers)})"
        )
        return {
            "message": f"Scanner autónomo iniciado con éxito (barrido cada {self.interval_seconds}s, {mode_label}).",
            "status": self.get_status(),
        }

    async def stop(self, reason: str = "MANUAL_STOP") -> dict[str, Any]:
        if not self.running:
            return {
                "message": "El scanner autónomo ya estaba detenido.",
                "status": self.get_status(),
                "last_audit_report": self.last_audit_report,
            }

        self.running = False
        self.last_stop_reason = reason
        self._session_end_monotonic = None
        self.session_ends_at = None
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

        # Al finalizar la sesión, el Agente Auditor Independiente ejecuta la auditoría crítica
        self.last_audit_report = await asyncio.to_thread(
            audit_agent.finalize_session_and_audit,
            reason,
            self.portfolio_value,
            self.market_watch,
            self.auto_apply_audit_fixes,
        )

        logger.info(f"Scanner autónomo DETENIDO ({reason}) y Auditado ({self.last_audit_report['audit_id']}).")
        return {
            "message": f"Scanner autónomo detenido ({reason}) y auditoría crítica generada.",
            "status": self.get_status(),
            "audit_report": self.last_audit_report,
        }

    def _has_open_exposure_in_ticker(self, ticker: str) -> bool:
        """Verifica en Alpaca o en el historial de sesión si ya hay orden/posición activa en ese ticker."""
        now_ts = time.time()
        last_exec = self.auto_executed_tickers_cooldown.get(ticker, 0.0)
        if (now_ts - last_exec) < self.auto_execution_cooldown_seconds:
            return True

        client, is_configured, _ = get_alpaca_trading_client()
        if is_configured and client is not None:
            try:
                from alpaca.trading.enums import QueryOrderStatus
                from alpaca.trading.requests import GetOrdersRequest

                open_orders = client.get_orders(
                    filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[ticker])
                )
                if open_orders:
                    return True
                positions = client.get_all_positions()
                if any(str(p.symbol).upper() == ticker for p in positions):
                    return True
            except Exception:
                pass
        return False

    async def execute_single_pass(self) -> list[dict[str, Any]]:
        """Ejecuta un barrido completo sin bloquear el event loop de FastAPI."""
        if self.is_scanning_now:
            return list(self.active_opportunities.values())

        self.is_scanning_now = True
        t0 = time.perf_counter()
        self.last_scan_started_at = datetime.now(timezone.utc).isoformat()

        try:
            # 1. Sincronizar equity real de Alpaca Paper y conciliar órdenes cerradas (TP/SL)
            if self.auto_sync_alpaca_equity and self.portfolio_source != "MANUAL_OVERRIDE":
                acct_info = await asyncio.to_thread(fetch_alpaca_account_sync)
                self._sync_effective_portfolio(acct_info, reset_baseline=False)

            # Conciliar con órdenes recientes de Alpaca para que el Agente Auditor sepa si saltó algún TP o SL
            try:
                orders_snap = await get_alpaca_orders(status="all")
                if orders_snap.get("orders"):
                    await asyncio.to_thread(
                        audit_agent.reconcile_with_alpaca_orders, orders_snap["orders"]
                    )
            except Exception:
                pass

            # 2. CORTAFUEGOS ESTRICTO DEL 4% DE PÉRDIDA DE CARTERA
            drawdown_state = audit_agent.update_session_equity(
                live_equity=self.portfolio_value,
                scans_executed=self.scan_count,
            )
            if drawdown_state.get("breached_4pct"):
                stop_msg = (
                    f"CIRCUIT_BREAKER_4PCT_LOSS_REACHED "
                    f"(Caída={drawdown_state['drawdown_pct']}% >= Límite={drawdown_state['limit_pct']}%)"
                )
                logger.critical(f"[CORTAFUEGOS 4%] {stop_msg}. Parando sistema y auditando...")
                self.running = False
                self.last_stop_reason = stop_msg
                self._stop_event.set()
                self.last_audit_report = await asyncio.to_thread(
                    audit_agent.finalize_session_and_audit,
                    stop_msg,
                    self.portfolio_value,
                    self.market_watch,
                    self.auto_apply_audit_fixes,
                )
                return list(self.active_opportunities.values())

            # 3. Ejecutar escaneo cuantitativo en hilo dedicado
            found_list, snapshots = await asyncio.to_thread(
                run_market_scan_sync,
                self.tickers,
                self.portfolio_value,
                self.force_test_signal,
            )
            if snapshots:
                self.market_watch = snapshots
                try:
                    with open(MARKET_WATCH_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(snapshots, f)
                except Exception as e:
                    logger.debug(f"Error guardando market_watch_latest.json: {e}")
            audit_agent.update_session_equity(
                live_equity=self.portfolio_value,
                scans_executed=self.scan_count + 1,
                opportunities_detected_delta=len(found_list),
            )

            # 4. Procesar oportunidades según modo (MANUAL vs FULL_AUTO)
            now_ts = time.time()
            new_for_webhook: list[dict[str, Any]] = []

            for opp in found_list:
                ticker = opp["ticker"]
                existing = self.active_opportunities.get(ticker)
                opp["opportunity_id"] = (
                    existing["opportunity_id"] if existing else f"opp-{ticker.lower()}-{ int(now_ts) }"
                )
                opp["first_seen_at"] = (
                    existing["first_seen_at"] if existing else opp["detected_at"]
                )

                params = audit_agent.strategy_params
                carried = (existing or {}).get("ai_advice")
                if carried and carried.get("status") in ("DONE", "PENDING"):
                    opp["ai_advice"] = carried

                if self.execution_mode == "FULL_AUTO":
                    already_exposed = await asyncio.to_thread(self._has_open_exposure_in_ticker, ticker)
                    if not already_exposed:
                        # Puerta del asesor IA: espera corta; solo un REJECT (o fallo con fail_open=False) veta.
                        if params.get("ai_gate_full_auto", True):
                            advice = opp.get("ai_advice") if (opp.get("ai_advice") or {}).get("status") == "DONE" else None
                            if advice is None:
                                advice = await self.request_advice(
                                    opp, timeout_seconds=float(params.get("ai_gate_timeout_seconds", 6.0))
                                )
                            opp["ai_advice"] = advice
                            fail_open = bool(params.get("ai_gate_fail_open", True))
                            vetoed = (advice.get("status") == "DONE" and advice.get("verdict") == "REJECT") or (
                                advice.get("status") != "DONE" and not fail_open
                            )
                            if vetoed:
                                opp["status"] = "VETOED_BY_AI"
                                self.active_opportunities[ticker] = opp
                                logger.info(
                                    f"[ASESOR IA] Entrada en {ticker} vetada: "
                                    f"{advice.get('headline') or advice.get('reason')}"
                                )
                                continue
                        auto_req = ExecuteOrderRequest(
                            ticker=ticker,
                            shares=int(opp["order_sizing"]["shares_to_buy"]),
                            entry_limit_price=float(opp["entry_price"]),
                            stop_loss_price=float(opp["order_sizing"]["stop_loss_price"]),
                            take_profit_price=float(opp["order_sizing"]["take_profit_price"]),
                            order_type="marketable_limit",
                            time_in_force="gtc",
                        )
                        exec_res = await asyncio.to_thread(submit_bracket_order_sync, auto_req)
                        if exec_res.get("status") in ("LIVE_PAPER_BRACKET_SENT", "SIMULATED_BRACKET_SENT"):
                            self.auto_executed_tickers_cooldown[ticker] = now_ts
                            opp["status"] = "AUTO_EXECUTED_BRACKET_OCO"
                            opp["execution"] = exec_res
                            self.execution_history.insert(0, exec_res)
                            await asyncio.to_thread(
                                audit_agent.record_trade_execution,
                                opp,
                                exec_res,
                                "FULL_AUTO",
                                "marketable_limit",
                                self.portfolio_value,
                            )
                            new_for_webhook.append(opp)
                            continue

                opp["status"] = "READY_FOR_MANUAL_APPROVAL"
                if not opp.get("ai_advice"):
                    cached = self._cached_advice(ticker)
                    opp["ai_advice"] = cached or {"status": "PENDING"}
                self.active_opportunities[ticker] = opp
                if (opp["ai_advice"] or {}).get("status") == "PENDING":
                    self.schedule_background_advice(ticker)

                last_notified = self.last_webhook_notified_at.get(ticker, 0.0)
                if (now_ts - last_notified) >= self.webhook_cooldown_seconds:
                    new_for_webhook.append(opp)
                    self.last_webhook_notified_at[ticker] = now_ts


            self.scan_count += 1
            self.last_scan_completed_at = datetime.now(timezone.utc).isoformat()
            self.last_scan_duration_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            self.last_error = None

            # 5. Si hay webhook configurado (ej. n8n -> Telegram móvil), empujar sin bloquear
            if new_for_webhook:
                asyncio.create_task(self._notify_webhook(new_for_webhook))

            return list(self.active_opportunities.values())

        except Exception as exc:
            self.last_error = str(exc)
            logger.error(f"Error en ciclo del scanner autónomo: {exc}")
            return list(self.active_opportunities.values())
        finally:
            self.is_scanning_now = False

    async def _notify_webhook(self, opportunities: list[dict[str, Any]]) -> None:
        cfg = reload_env_config()
        webhook_url = cfg["n8n_webhook_url"]
        if not webhook_url:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    webhook_url,
                    json={
                        "event": "NEW_TRADING_OPPORTUNITIES",
                        "execution_mode": self.execution_mode,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "opportunities_found": len(opportunities),
                        "data": opportunities,
                    },
                )
        except Exception as exc:
            logger.warning(f"No se pudo notificar al webhook {webhook_url}: {exc}")

    async def _run_loop(self) -> None:
        while self.running and not self._stop_event.is_set():
            if self._session_end_monotonic is not None and time.monotonic() >= self._session_end_monotonic:
                reason = f"SESSION_TIMER_COMPLETED ({self.session_duration_minutes}m)"
                self.running = False
                self.last_stop_reason = reason
                self._session_end_monotonic = None
                self.session_ends_at = None
                self.last_audit_report = await asyncio.to_thread(
                    audit_agent.finalize_session_and_audit,
                    reason,
                    self.portfolio_value,
                    self.market_watch,
                    self.auto_apply_audit_fixes,
                )
                logger.info(f"Sesión del scanner finalizada automáticamente y auditada ({reason}).")
                break

            await self.execute_single_pass()
            if not self.running:
                break

            wait_timeout = float(self.interval_seconds)
            if self._session_end_monotonic is not None:
                remaining = self._session_end_monotonic - time.monotonic()
                if remaining <= 0:
                    reason = f"SESSION_TIMER_COMPLETED ({self.session_duration_minutes}m)"
                    self.running = False
                    self.last_stop_reason = reason
                    self._session_end_monotonic = None
                    self.session_ends_at = None
                    self.last_audit_report = await asyncio.to_thread(
                        audit_agent.finalize_session_and_audit,
                        reason,
                        self.portfolio_value,
                        self.market_watch,
                        self.auto_apply_audit_fixes,
                    )
                    logger.info(f"Sesión del scanner finalizada automáticamente y auditada ({reason}).")
                    break
                wait_timeout = min(wait_timeout, remaining)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=wait_timeout,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_status(self) -> dict[str, Any]:
        remaining_sec: Optional[int] = None
        if self.running and self._session_end_monotonic is not None:
            remaining_sec = max(0, int(round(self._session_end_monotonic - time.monotonic())))

        sess = audit_agent.current_session
        alloc_pct, alloc_label = get_adaptive_allocation_pct(
            self.portfolio_value,
            audit_agent.strategy_params,
        )
        return {
            "running": self.running,
            "is_scanning_now": self.is_scanning_now,
            "execution_mode": self.execution_mode,
            "interval_seconds": self.interval_seconds,
            "session_mode": "TIMED" if self.session_duration_minutes else "UNLIMITED",
            "session_duration_minutes": self.session_duration_minutes,
            "session_ends_at": self.session_ends_at,
            "session_remaining_seconds": remaining_sec,
            "max_session_drawdown_pct": round(self.max_session_drawdown_pct * 100, 2),
            "current_session_drawdown_pct": (
                sess.get("current_drawdown_from_initial_pct", 0.0) if sess else 0.0
            ),
            "hard_stop_equity_4pct": (
                sess.get("hard_stop_equity_4pct")
                if sess
                else round(self.portfolio_value * (1.0 - self.max_session_drawdown_pct), 2)
            ),
            "auto_apply_audit_fixes": self.auto_apply_audit_fixes,
            "last_stop_reason": self.last_stop_reason,
            "last_audit_id": self.last_audit_report.get("audit_id") if self.last_audit_report else None,
            "tickers": self.tickers,
            "tickers_count": len(self.tickers),
            "portfolio_value": round(self.portfolio_value, 2),
            "portfolio_source": self.portfolio_source,
            "effective_allocation_tier": {
                "tier_label": alloc_label,
                "max_allocation_pct": round(alloc_pct * 100, 1),
                "max_capital_per_trade": round(self.portfolio_value * alloc_pct, 2),
                "max_risk_1pct_per_trade": round(self.portfolio_value * 0.01, 2),
            },
            "auto_sync_alpaca_equity": self.auto_sync_alpaca_equity,
            "force_test_signal": self.force_test_signal,
            "scan_count": self.scan_count,
            "started_at": self.started_at,
            "elapsed_seconds": (
                max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(self.started_at)).total_seconds()))
                if self.running and self.started_at
                else 0
            ),
            "last_scan_started_at": self.last_scan_started_at,
            "last_scan_completed_at": self.last_scan_completed_at,
            "last_scan_duration_ms": self.last_scan_duration_ms,
            "active_opportunities_count": len(self.active_opportunities),
            "market_watch_count": len(self.market_watch),
            "strategy_params_version": audit_agent.strategy_params.get("version", 1),
            "macro_regime": _MACRO_REGIME_STATE["last_regime"],
            "last_error": self.last_error,
        }


scanner_worker = AutonomousScannerWorker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = reload_env_config()
    try:
        await asyncio.to_thread(evaluate_macro_regime_sync, None, True)
    except Exception as exc:
        logger.debug(f"Inicialización de régimen macro diferida: {exc}")
    if cfg["auto_start"]:
        await scanner_worker.start()
    elif not scanner_worker.market_watch:
        asyncio.create_task(scanner_worker.execute_single_pass())
    yield
    if scanner_worker.running:
        await scanner_worker.stop(reason="SERVER_SHUTDOWN")


app = FastAPI(
    title="Motor Cuantitativo Agéntico (Estocástico 14/4 & Riesgo 1% + Alpaca Paper OCO + Auditor Crítico)",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# ENDPOINTS CORE (COMPATIBLES CON N8N Y DOCUMENTACIÓN SPEC.MD)
# =====================================================================

@app.get("/health")
def health_check():
    acct = fetch_alpaca_account_sync()
    return {
        "status": "ok",
        "service": "quant-engine-active",
        "scanner_running": scanner_worker.running,
        "execution_mode": scanner_worker.execution_mode,
        "scan_interval_seconds": scanner_worker.interval_seconds,
        "active_opportunities": len(scanner_worker.active_opportunities),
        "alpaca_mode": acct.get("mode"),
        "alpaca_connected": acct.get("connected"),
        "portfolio_equity": acct.get("equity"),
        "strategy_version": audit_agent.strategy_params.get("version", 1),
        "macro_regime_status": _MACRO_REGIME_STATE["last_regime"].get("status"),
    }


@app.post("/scan-market")
async def scan_market(payload: ScanRequest):
    opportunities, snapshots = await asyncio.to_thread(
        run_market_scan_sync,
        payload.tickers,
        payload.portfolio_value,
        payload.force_test_signal,
    )
    scanner_worker.market_watch = snapshots
    for opp in opportunities:
        ticker = opp["ticker"]
        opp["opportunity_id"] = f"opp-{ticker.lower()}-{int(time.time())}"
        opp["status"] = "READY_FOR_MANUAL_APPROVAL"
        scanner_worker.active_opportunities[ticker] = opp

    return {
        "scanned_tickers": payload.tickers,
        "opportunities_found": len(opportunities),
        "macro_regime": _MACRO_REGIME_STATE["last_regime"],
        "data": opportunities,
        "market_watch": snapshots,
    }


@app.post("/validate-risk")
def validate_trade_risk(candidate: TradeCandidate):
    return evaluate_risk_math(
        candidate.portfolio_value,
        candidate.entry_price,
        candidate.stop_loss_price,
        candidate.take_profit_price,
    )


@app.post("/execute-bracket")
async def execute_bracket_order(order: ExecuteOrderRequest):
    """
    Envía una Orden Bracket (Entrada + Stop Loss & Take Profit Automáticos OCO) al bróker
    y la registra en el Agente Auditor Independiente.
    """
    result = await asyncio.to_thread(submit_bracket_order_sync, order)
    if result.get("status") in ("LIVE_PAPER_BRACKET_SENT", "SIMULATED_BRACKET_SENT"):
        scanner_worker.execution_history.insert(0, result)
        opp = scanner_worker.active_opportunities.pop(order.ticker.upper(), None) or {
            "ticker": order.ticker.upper(),
            "entry_price": order.entry_limit_price,
            "order_sizing": {
                "shares_to_buy": order.shares,
                "stop_loss_price": order.stop_loss_price,
                "take_profit_price": order.take_profit_price,
            },
        }
        await asyncio.to_thread(
            audit_agent.record_trade_execution,
            opp,
            result,
            "MANUAL",
            order.order_type,
            scanner_worker.portfolio_value,
        )
    return result


# =====================================================================
# ENDPOINTS DEL WORKER AUTÓNOMO DE ESCANEO (FASE 1 — 15s START/STOP)
# =====================================================================

@app.get("/scanner/status")
def get_scanner_status():
    if _MACRO_REGIME_STATE["last_fetched_at"] == 0.0:
        evaluate_macro_regime_sync()
    return scanner_worker.get_status()


@app.get("/scanner/macro-regime")
async def get_macro_regime(refresh: bool = Query(default=False)):
    regime = await asyncio.to_thread(
        evaluate_macro_regime_sync,
        scanner_worker.market_watch or None,
        refresh,
    )
    return regime


@app.post("/scanner/start")
async def start_scanner(payload: Optional[ScannerStartRequest] = None):
    return await scanner_worker.start(payload)


@app.post("/scanner/stop")
async def stop_scanner():
    return await scanner_worker.stop(reason="MANUAL_STOP")


@app.post("/scanner/scan-now")
async def trigger_immediate_scan(force_test_signal: bool = Query(default=False)):
    prev_force = scanner_worker.force_test_signal
    scanner_worker.force_test_signal = force_test_signal
    try:
        opportunities = await scanner_worker.execute_single_pass()
        return {
            "status": scanner_worker.get_status(),
            "opportunities_found": len(opportunities),
            "opportunities": opportunities,
            "market_watch": scanner_worker.market_watch,
        }
    finally:
        scanner_worker.force_test_signal = prev_force


@app.get("/scanner/opportunities")
def list_active_opportunities():
    opps = list(scanner_worker.active_opportunities.values())
    return {
        "count": len(opps),
        "scanner_running": scanner_worker.running,
        "execution_mode": scanner_worker.execution_mode,
        "last_scan_completed_at": scanner_worker.last_scan_completed_at,
        "opportunities": opps,
        "market_watch": scanner_worker.market_watch,
        "recent_executions": scanner_worker.execution_history[:20],
    }


@app.post("/scanner/approve/{ticker}")
async def approve_opportunity_one_click(
    ticker: str,
    req: Optional[ApproveOpportunityRequest] = None,
):
    """
    ENTRADA MANUAL DE 1 CLIC + SALIDA AUTOMÁTICA INMEDIATA (OCO):
    Toma la oportunidad pre-calculada de la cola en vivo, refresca el precio actual si se solicita,
    verifica la regla del 1% de riesgo, lanza la orden Bracket OCO a Alpaca Paper al instante
    y la registra en el Agente Auditor Independiente.
    """
    symbol = ticker.strip().upper()
    opp = scanner_worker.active_opportunities.get(symbol)
    if not opp:
        raise HTTPException(
            status_code=404,
            detail=f"No hay ninguna oportunidad activa en bandeja para {symbol}.",
        )

    opts = req or ApproveOpportunityRequest()
    entry_price = float(opp["entry_price"])
    stop_loss_price = float(opp["order_sizing"]["stop_loss_price"])
    take_profit_price = float(opp["order_sizing"]["take_profit_price"])
    shares = int(opp["order_sizing"]["shares_to_buy"])
    portfolio_value = float(opp.get("portfolio_value_used", scanner_worker.portfolio_value))

    if opts.refresh_live_price and "SIMULACIÓN" not in opp.get("market_regime", ""):
        live_snap = await asyncio.to_thread(fetch_latest_price_and_atr_sync, symbol)
        if live_snap and live_snap["price"] > 0 and live_snap["atr"] > 0:
            atr_mult = float(audit_agent.strategy_params.get("atr_stop_loss_multiplier", 1.5))
            tp_rr = float(audit_agent.strategy_params.get("take_profit_reward_risk_ratio", 2.0))
            entry_price = live_snap["price"]
            stop_loss_price = round(entry_price - (atr_mult * live_snap["atr"]), 2)
            risk_dist = entry_price - stop_loss_price
            take_profit_price = round(entry_price + (tp_rr * risk_dist), 2)

            revalidation = evaluate_risk_math(
                portfolio_value, entry_price, stop_loss_price, take_profit_price
            )
            if not revalidation["viable"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"La cotización se movió a ${entry_price} y ya no supera el filtro del 1%: {revalidation['reason']}",
                )
            shares = int(revalidation["order_sizing"]["shares_to_buy"])
            opp["entry_price"] = entry_price
            opp["order_sizing"] = revalidation["order_sizing"]
            opp["financial_metrics"] = revalidation["financial_metrics"]

    if opts.override_shares is not None:
        shares = opts.override_shares

    order_req = ExecuteOrderRequest(
        ticker=symbol,
        shares=shares,
        entry_limit_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        order_type=opts.order_type,
        slippage_buffer_pct=opts.slippage_buffer_pct,
        time_in_force=opts.time_in_force,
    )

    execution_result = await asyncio.to_thread(submit_bracket_order_sync, order_req)
    trade_audit_record = None
    if execution_result.get("status") in ("LIVE_PAPER_BRACKET_SENT", "SIMULATED_BRACKET_SENT"):
        scanner_worker.active_opportunities.pop(symbol, None)
        scanner_worker.execution_history.insert(0, execution_result)
        trade_audit_record = await asyncio.to_thread(
            audit_agent.record_trade_execution,
            opp,
            execution_result,
            "MANUAL",
            opts.order_type,
            portfolio_value,
        )

    return {
        "approved_ticker": symbol,
        "execution": execution_result,
        "audit_record": trade_audit_record,
    }


@app.delete("/scanner/opportunities/{ticker}")
def dismiss_opportunity(ticker: str):
    symbol = ticker.strip().upper()
    removed = scanner_worker.active_opportunities.pop(symbol, None)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No existe oportunidad activa para {symbol}.")
    return {"status": "DISMISSED", "ticker": symbol}


# =====================================================================
# ENDPOINTS DEL AGENTE AUDITOR INDEPENDIENTE Y AUTO-CORRECCIÓN
# =====================================================================

@app.get("/audit/status")
def get_audit_agent_status():
    """Estado en vivo del Agente Auditor Independiente, sesión activa, drawdown hacia el 4% y parámetros."""
    return {
        "agent": "quant-independent-auditor-active",
        "current_session": audit_agent.current_session,
        "total_recorded_trades": len(audit_agent.trades_cache),
        "total_audit_reports": len(audit_agent.audit_reports),
        "latest_audit_report": audit_agent.audit_reports[0] if audit_agent.audit_reports else None,
        "active_strategy_params": audit_agent.strategy_params,
    }


@app.get("/audit/trades")
async def get_audited_trades(reconcile_live: bool = Query(default=True)):
    """Devuelve todas las operaciones registradas con su contexto técnico y resultado real en Alpaca."""
    if reconcile_live:
        try:
            orders_snap = await get_alpaca_orders(status="all")
            if orders_snap.get("orders"):
                await asyncio.to_thread(
                    audit_agent.reconcile_with_alpaca_orders, orders_snap["orders"]
                )
        except Exception:
            pass
    return {
        "count": len(audit_agent.trades_cache),
        "trades": audit_agent.trades_cache[:100],
    }


@app.get("/audit/reports")
def get_audit_reports():
    """Lista los informes críticos generados por el Agente Auditor al finalizar sesiones."""
    return {
        "count": len(audit_agent.audit_reports),
        "reports": audit_agent.audit_reports[:50],
    }


@app.post("/audit/run")
async def run_critical_audit_now(
    auto_apply_fixes: bool = Query(
        default=False,
        description="Si es True, aplica inmediatamente las correcciones de parámetros recomendadas por la auditoría.",
    )
):
    """Ejecuta bajo demanda una auditoría crítica sobre las operaciones registradas."""
    try:
        orders_snap = await get_alpaca_orders(status="all")
        if orders_snap.get("orders"):
            await asyncio.to_thread(
                audit_agent.reconcile_with_alpaca_orders, orders_snap["orders"]
            )
    except Exception:
        pass

    report = await asyncio.to_thread(
        audit_agent.generate_critical_audit_report,
        audit_agent.current_session["session_id"] if audit_agent.current_session else None,
        "MANUAL_AUDIT_REQUEST",
        scanner_worker.market_watch,
        auto_apply_fixes,
    )
    scanner_worker.last_audit_report = report
    return report


@app.post("/audit/apply-recommendations/{audit_id}")
def apply_audit_recommendations(audit_id: str):
    """Aplica con 1 clic las mejoras de parámetros propuestas por un informe de auditoría crítica."""
    try:
        return audit_agent.apply_audit_recommendations(audit_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/strategy/params")
def get_strategy_parameters():
    return audit_agent.strategy_params


@app.put("/strategy/params")
def update_strategy_parameters(new_params: dict[str, Any]):
    return audit_agent.save_strategy_params(new_params, updated_by="MANUAL_TUNING")


@app.post("/strategy/reset")
def reset_strategy_parameters():
    return audit_agent.reset_strategy_params()


@app.get("/backtest/latest")
def get_latest_backtest():
    """Devuelve los resultados cuantitativos del último backtest Full Auto guardado en disco."""
    candidates = [
        Path(__file__).parent / "audit_data" / "backtest_results.json",
        Path("app/audit_data/backtest_results.json"),
        Path("python_engine/app/audit_data/backtest_results.json"),
    ]
    p = next((f for f in candidates if f.exists()), None)
    if not p:
        raise HTTPException(status_code=404, detail="No hay informe de backtest disponible.")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/backtest/run")
async def trigger_backtest():
    """Ejecuta un nuevo backtest cuantitativo Full Auto bajo demanda en hilo secundario."""
    from backtest_engine import run_full_auto_backtest
    report = await asyncio.to_thread(run_full_auto_backtest)
    return report


# =====================================================================
# ENDPOINTS DE CONEXIÓN Y MONITORIZACIÓN EN VIVO CON ALPACA PAPER
# =====================================================================

@app.get("/alpaca/account")
async def get_alpaca_account():
    return await asyncio.to_thread(fetch_alpaca_account_sync)


@app.post("/alpaca/credentials")
async def configure_alpaca_credentials(creds: AlpacaCredentialsRequest):
    """
    Valida en vivo un par de claves de Alpaca Paper Trading y, si son válidas,
    las guarda automáticamente en .env para activar el modo LIVE_PAPER sin reiniciar.
    """
    from alpaca.trading.client import TradingClient

    try:
        test_client = TradingClient(creds.api_key.strip(), creds.secret_key.strip(), paper=creds.paper)
        acct = await asyncio.to_thread(test_client.get_account)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Credenciales rechazadas por Alpaca: {str(exc)}",
        )

    try:
        set_key(ENV_FILE_PATH, "ALPACA_API_KEY", creds.api_key.strip(), quote_mode="never")
        set_key(ENV_FILE_PATH, "ALPACA_SECRET_KEY", creds.secret_key.strip(), quote_mode="never")
        set_key(
            ENV_FILE_PATH,
            "ALPACA_PAPER",
            "true" if creds.paper else "false",
            quote_mode="never",
        )
    except Exception as file_exc:
        logger.warning(f"No se pudo persistir en {ENV_FILE_PATH}, aplicando en memoria: {file_exc}")

    os.environ["ALPACA_API_KEY"] = creds.api_key.strip()
    os.environ["ALPACA_SECRET_KEY"] = creds.secret_key.strip()
    os.environ["ALPACA_PAPER"] = "true" if creds.paper else "false"

    return {
        "status": "CONNECTED_AND_SAVED",
        "mode": "LIVE_PAPER" if creds.paper else "LIVE_BROKER",
        "account_id": str(acct.id),
        "equity": float(acct.equity),
        "buying_power": float(acct.buying_power),
        "currency": str(acct.currency),
    }


@app.get("/alpaca/positions")
async def get_alpaca_positions():
    def _fetch():
        client, is_configured, _ = get_alpaca_trading_client()
        if not is_configured or client is None:
            return {
                "mode": "SIMULATION_FALLBACK",
                "count": 0,
                "positions": [],
            }
        try:
            raw_positions = client.get_all_positions()
            positions = [
                {
                    "symbol": str(p.symbol),
                    "qty": float(p.qty),
                    "side": str(p.side.value if hasattr(p.side, "value") else p.side),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": round(float(p.unrealized_plpc) * 100, 2),
                }
                for p in raw_positions
            ]
            return {"mode": "LIVE_PAPER", "count": len(positions), "positions": positions}
        except Exception as exc:
            return {"mode": "ERROR", "detail": str(exc), "positions": []}

    return await asyncio.to_thread(_fetch)


@app.get("/alpaca/orders")
async def get_alpaca_orders(status: Literal["open", "closed", "all"] = Query(default="open")):
    """Lista las órdenes en Alpaca incluyendo las patas hijas (Stop Loss y Take Profit OCO)."""
    def _fetch():
        client, is_configured, _ = get_alpaca_trading_client()
        if not is_configured or client is None:
            return {
                "mode": "SIMULATION_FALLBACK",
                "count": len(SIMULATED_ORDERS_HISTORY),
                "orders": SIMULATED_ORDERS_HISTORY,
            }
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            status_map = {
                "open": QueryOrderStatus.OPEN,
                "closed": QueryOrderStatus.CLOSED,
                "all": QueryOrderStatus.ALL,
            }
            req = GetOrdersRequest(status=status_map[status], limit=50, nested=True)
            raw_orders = client.get_orders(filter=req)

            def serialize_order(o) -> dict[str, Any]:
                legs = getattr(o, "legs", None) or []
                return {
                    "id": str(o.id),
                    "symbol": str(o.symbol),
                    "qty": float(o.qty) if o.qty is not None else None,
                    "filled_qty": float(o.filled_qty) if o.filled_qty is not None else 0.0,
                    "side": str(o.side.value if hasattr(o.side, "value") else o.side),
                    "type": str(o.type.value if hasattr(o.type, "value") else o.type),
                    "order_class": str(
                        o.order_class.value if hasattr(o.order_class, "value") else o.order_class
                    ),
                    "status": str(o.status.value if hasattr(o.status, "value") else o.status),
                    "limit_price": float(o.limit_price) if o.limit_price is not None else None,
                    "stop_price": float(o.stop_price) if o.stop_price is not None else None,
                    "filled_avg_price": (
                        float(o.filled_avg_price) if o.filled_avg_price is not None else None
                    ),
                    "created_at": str(o.created_at),
                    "legs": [
                        {
                            "id": str(leg.id),
                            "side": str(leg.side.value if hasattr(leg.side, "value") else leg.side),
                            "type": str(leg.type.value if hasattr(leg.type, "value") else leg.type),
                            "status": str(
                                leg.status.value if hasattr(leg.status, "value") else leg.status
                            ),
                            "limit_price": (
                                float(leg.limit_price) if leg.limit_price is not None else None
                            ),
                            "stop_price": (
                                float(leg.stop_price) if leg.stop_price is not None else None
                            ),
                        }
                        for leg in legs
                    ],
                }

            orders = [serialize_order(o) for o in raw_orders]
            return {"mode": "LIVE_PAPER", "count": len(orders), "orders": orders}
        except Exception as exc:
            return {"mode": "ERROR", "detail": str(exc), "orders": []}

    return await asyncio.to_thread(_fetch)


@app.delete("/alpaca/orders/{order_id}")
async def cancel_alpaca_order(order_id: str):
    def _cancel():
        client, is_configured, _ = get_alpaca_trading_client()
        if not is_configured or client is None:
            global SIMULATED_ORDERS_HISTORY
            SIMULATED_ORDERS_HISTORY = [
                o for o in SIMULATED_ORDERS_HISTORY if o.get("order_id") != order_id
            ]
            return {"status": "SIMULATED_ORDER_CANCELLED", "order_id": order_id}
        try:
            client.cancel_order_by_id(order_id)
            return {"status": "LIVE_ORDER_CANCELLED", "order_id": order_id}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return await asyncio.to_thread(_cancel)


@app.post("/alpaca/close-position/{ticker}")
async def close_alpaca_position_immediately(ticker: str):
    """Botón de pánico / salida manual inmediata a mercado cancelando órdenes OCO asociadas."""
    symbol = ticker.strip().upper()

    def _close():
        client, is_configured, _ = get_alpaca_trading_client()
        if not is_configured or client is None:
            return {"status": "SIMULATED_POSITION_CLOSED", "symbol": symbol}
        try:
            from alpaca.trading.requests import ClosePositionRequest

            closed = client.close_position(
                symbol, close_options=ClosePositionRequest(percentage="100")
            )
            return {
                "status": "LIVE_POSITION_CLOSING",
                "symbol": symbol,
                "closing_order_id": str(closed.id),
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return await asyncio.to_thread(_close)

# =====================================================================
# ENDPOINTS PARA LA GUI (FASE 2): VELAS, RESUMEN DE CARTERA, WEBSOCKET
# =====================================================================

_CANDLES_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CANDLES_CACHE_TTL_SECONDS = 10.0


def build_candles_payload_sync(ticker: str, bars: int) -> dict[str, Any]:
    """Velas diarias OHLCV + EMA(20) + SMA(50) + SMA(200) + diagnóstico experto corto plazo + Bracket OCO."""
    df = yf.download(ticker, period="1y", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError(f"Sin datos de mercado para {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 30:
        raise ValueError(f"Histórico insuficiente para {ticker}")

    close, high, low = df["Close"], df["High"], df["Low"]
    ema20 = EMAIndicator(close=close, window=20).ema_indicator()
    sma50 = SMAIndicator(close=close, window=50).sma_indicator()
    sma200 = SMAIndicator(close=close, window=200).sma_indicator()
    atr = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

    _, snap = analyze_single_ticker_df(ticker, df, scanner_worker.portfolio_value)

    tail = df.tail(bars)
    candles = [
        {
            "time": idx.strftime("%Y-%m-%d"),
            "open": round(float(r["Open"]), 2),
            "high": round(float(r["High"]), 2),
            "low": round(float(r["Low"]), 2),
            "close": round(float(r["Close"]), 2),
            "volume": int(r["Volume"]) if not pd.isna(r["Volume"]) else 0,
        }
        for idx, r in tail.iterrows()
    ]
    ema20_series = [
        {"time": idx.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
        for idx, v in ema20.loc[tail.index].items()
        if not pd.isna(v)
    ]
    sma50_series = [
        {"time": idx.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
        for idx, v in sma50.loc[tail.index].items()
        if not pd.isna(v)
    ]
    sma_series = [
        {"time": idx.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
        for idx, v in sma200.loc[tail.index].items()
        if not pd.isna(v)
    ]

    last = float(close.iloc[-1])
    last_atr = float(atr.iloc[-1])
    last_sma = float(sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else None
    params = audit_agent.strategy_params
    atr_mult = float(params.get("atr_stop_loss_multiplier", 1.5))
    tp_rr = float(params.get("take_profit_reward_risk_ratio", 2.0))

    opp = scanner_worker.active_opportunities.get(ticker)
    if opp:
        entry = float(opp["entry_price"])
        sl = float(opp["order_sizing"]["stop_loss_price"])
        tp = float(opp["order_sizing"]["take_profit_price"])
        source = "ACTIVE_OPPORTUNITY"
    else:
        entry = round(last, 2)
        sl = round(entry - atr_mult * last_atr, 2)
        tp = round(entry + tp_rr * (entry - sl), 2)
        source = "PROVISIONAL_FROM_LAST_CLOSE"
    risk = evaluate_risk_math(scanner_worker.portfolio_value, entry, sl, tp)

    return {
        "ticker": ticker,
        "interval": "1d",
        "candles": candles,
        "ema20_series": ema20_series,
        "sma50_series": sma50_series,
        "sma200_series": sma_series,
        "indicators": {
            "price": round(last, 2),
            "ema_20": snap.get("ema_20") if snap else None,
            "sma_50": snap.get("sma_50") if snap else None,
            "sma_200": round(last_sma, 2) if last_sma is not None else None,
            "stoch_k": snap.get("stoch_k", 50.0) if snap else 50.0,
            "stoch_d": snap.get("stoch_d", 50.0) if snap else 50.0,
            "rsi_14": snap.get("rsi_14", 50.0) if snap else 50.0,
            "adx": snap.get("adx", 20.0) if snap else 20.0,
            "plus_di": snap.get("plus_di", 20.0) if snap else 20.0,
            "minus_di": snap.get("minus_di", 20.0) if snap else 20.0,
            "di_spread": snap.get("di_spread", 0.0) if snap else 0.0,
            "atr_14": round(last_atr, 2),
            "rvol_20": snap.get("rvol_20", 1.0) if snap else 1.0,
            "ret_1d_pct": snap.get("ret_1d_pct", 0.0) if snap else 0.0,
            "ret_5d_pct": snap.get("ret_5d_pct", 0.0) if snap else 0.0,
            "close_location_value": snap.get("close_location_value", 0.5) if snap else 0.5,
            "candle_body_atr": snap.get("candle_body_atr", 0.0) if snap else 0.0,
            "falling_knife": snap.get("falling_knife", False) if snap else False,
            "short_term_bias": snap.get("short_term_bias", "NEUTRAL_WAIT") if snap else "NEUTRAL_WAIT",
            "quant_score": snap.get("quant_score", 50) if snap else 50,
            "expert_summary": snap.get("expert_summary", "") if snap else "",
            "expert_criteria": snap.get("expert_criteria", []) if snap else [],
            "regime": snap.get("market_regime", "NEUTRAL") if snap else "NEUTRAL",
        },
        "bracket_preview": {
            "source": source,
            "entry_price": entry,
            "stop_loss_price": sl,
            "take_profit_price": tp,
            "viable": risk.get("viable", False),
            "reason": risk.get("reason"),
            "order_sizing": risk.get("order_sizing"),
            "financial_metrics": risk.get("financial_metrics"),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/market/candles/{ticker}")
async def get_market_candles(ticker: str, bars: int = Query(default=120, ge=30, le=252)):
    symbol = ticker.strip().upper()
    key = f"{symbol}:{bars}"
    cached = _CANDLES_CACHE.get(key)
    now_m = time.monotonic()
    # Con oportunidad activa no se usa caché: los niveles SL/TP deben ser siempre los vigentes
    if cached and (now_m - cached[0]) < _CANDLES_CACHE_TTL_SECONDS and symbol not in scanner_worker.active_opportunities:
        return cached[1]
    try:
        payload = await asyncio.to_thread(build_candles_payload_sync, symbol, bars)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error obteniendo velas de {symbol}: {exc}")
    _CANDLES_CACHE[key] = (now_m, payload)
    return payload


def compute_performance_metrics() -> dict[str, Any]:
    """Win rate, profit factor, R:R medio y curva de equidad a partir de operaciones cerradas reales."""
    closed = [
        t for t in audit_agent.trades_cache
        if t.get("lifecycle", {}).get("realized_pnl_usd") is not None
    ]
    closed.sort(key=lambda t: t.get("closed_at") or t.get("opened_at") or "")
    pnls = [float(t["lifecycle"]["realized_pnl_usd"]) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    base = float(audit_agent.strategy_params.get("virtual_portfolio_base") or scanner_worker.portfolio_value)
    curve = [{"timestamp": None, "equity": round(base, 2)}]
    running = base
    for t, p in zip(closed, pnls):
        running += p
        curve.append({"timestamp": t.get("closed_at"), "equity": round(running, 2)})
    r_vals = [
        float(t["lifecycle"]["r_multiple_achieved"])
        for t in closed
        if t["lifecycle"].get("r_multiple_achieved") is not None
    ]
    return {
        "total_closed_trades": len(closed),
        "win_rate_pct": round(len(wins) / len(closed) * 100.0, 1) if closed else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (None if not wins else 999.0),
        "avg_rr_ratio": round(sum(r_vals) / len(r_vals), 2) if r_vals else None,
        "net_realized_pnl_usd": round(sum(pnls), 2),
        "equity_curve": curve,
    }


def closed_trades_view() -> list[dict[str, Any]]:
    """Historial de operaciones cerradas listo para la tabla de la GUI."""
    rows = []
    for t in audit_agent.trades_cache:
        lc = t.get("lifecycle", {})
        if lc.get("realized_pnl_usd") is None:
            continue
        duration = None
        try:
            if t.get("opened_at") and t.get("closed_at"):
                duration = int(
                    (datetime.fromisoformat(t["closed_at"]) - datetime.fromisoformat(t["opened_at"])).total_seconds()
                )
        except Exception:
            pass
        rows.append(
            {
                "trade_id": t.get("trade_id"),
                "ticker": t.get("ticker"),
                "shares": t.get("risk_and_sizing", {}).get("shares"),
                "entry_price": lc.get("filled_avg_price") or t.get("technical_context_at_entry", {}).get("entry_reference_price"),
                "exit_price": lc.get("exit_price"),
                "exit_reason": lc.get("exit_reason"),
                "opened_at": t.get("opened_at"),
                "closed_at": t.get("closed_at"),
                "duration_seconds": duration,
                "realized_pnl_usd": lc.get("realized_pnl_usd"),
                "realized_pnl_pct": lc.get("realized_pnl_pct"),
                "execution_mode": t.get("execution_mode"),
            }
        )
    return rows[:100]


async def build_live_summary() -> dict[str, Any]:
    acct = await asyncio.to_thread(fetch_alpaca_account_sync)
    if scanner_worker.auto_sync_alpaca_equity and scanner_worker.portfolio_source != "MANUAL_OVERRIDE":
        scanner_worker._sync_effective_portfolio(acct, reset_baseline=False)
    effective = scanner_worker.portfolio_value

    positions_snap = await get_alpaca_positions()
    orders_snap = await get_alpaca_orders(status="all")

    # Niveles SL/TP vigentes de cada posición, extraídos de las patas hijas del Bracket OCO
    levels: dict[str, dict[str, Optional[float]]] = {}
    for o in orders_snap.get("orders", []):
        for leg in o.get("legs", []) or []:
            if str(leg.get("status", "")).lower() not in ("new", "held", "accepted", "pending_new", "partially_filled"):
                continue
            lv = levels.setdefault(str(o["symbol"]), {"stop_loss_price": None, "take_profit_price": None})
            if leg.get("stop_price") is not None:
                lv["stop_loss_price"] = leg["stop_price"]
            if leg.get("limit_price") is not None:
                lv["take_profit_price"] = leg["limit_price"]

    active_positions = []
    open_risk_usd = 0.0
    for p in positions_snap.get("positions", []):
        lv = levels.get(p["symbol"], {})
        sl, tp = lv.get("stop_loss_price"), lv.get("take_profit_price")
        progress = None
        if sl is not None and tp is not None and tp > sl:
            progress = round(min(100.0, max(0.0, (p["current_price"] - sl) / (tp - sl) * 100.0)), 1)
        if sl is not None:
            open_risk_usd += max(0.0, (p["avg_entry_price"] - sl) * p["qty"])
        active_positions.append(
            {**p, "stop_loss_price": sl, "take_profit_price": tp, "sl_to_tp_progress_pct": progress}
        )

    sess = audit_agent.current_session or {}
    dd_limit = scanner_worker.max_session_drawdown_pct
    return {
        "effective_portfolio_value": round(effective, 2),
        "portfolio_source": scanner_worker.portfolio_source,
        "alpaca_connected": acct.get("connected"),
        "alpaca_mode": acct.get("mode"),
        "alpaca_raw_equity": acct.get("equity"),
        "buying_power": acct.get("buying_power"),
        "cash": acct.get("cash"),
        "daily_pnl_usd": acct.get("daily_pnl"),
        "daily_pnl_pct": (
            round(acct["daily_pnl"] / acct["last_equity"] * 100.0, 3)
            if acct.get("daily_pnl") is not None and acct.get("last_equity")
            else None
        ),
        "open_risk_exposure_usd": round(open_risk_usd, 2),
        "open_risk_exposure_pct": round(open_risk_usd / effective * 100.0, 2) if effective > 0 else 0.0,
        "hard_stop_4pct_price": sess.get("hard_stop_equity_4pct") or round(effective * (1.0 - dd_limit), 2),
        "current_drawdown_pct": sess.get("current_drawdown_from_initial_pct", 0.0),
        "active_positions": active_positions,
        "closed_trades": closed_trades_view(),
        "performance": compute_performance_metrics(),
        "macro_regime": _MACRO_REGIME_STATE["last_regime"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/portfolio/live-summary")
async def get_portfolio_live_summary():
    return await build_live_summary()


@app.post("/engine/start")
async def engine_start(payload: Optional[ScannerStartRequest] = None):
    return await scanner_worker.start(payload)


@app.post("/engine/stop")
async def engine_stop():
    return await scanner_worker.stop(reason="MANUAL_STOP")


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """
    Canal en tiempo real para la GUI. Emite un STATE_SNAPSHOT al conectar y cada 2 s, y eventos
    puntuales: OPPORTUNITY_ALERT, ORDER_EXECUTED, CIRCUIT_BREAKER_HALT, SESSION_AUDIT_READY, MACRO_REGIME_UPDATE.
    """
    await ws.accept()
    seen_opps = set(scanner_worker.active_opportunities.keys())
    seen_exec = len(scanner_worker.execution_history)
    seen_audit = scanner_worker.last_audit_report.get("audit_id") if scanner_worker.last_audit_report else None
    seen_macro_status = _MACRO_REGIME_STATE["last_regime"].get("status")
    was_running = scanner_worker.running
    try:
        while True:
            events: list[dict[str, Any]] = []
            for t, opp in list(scanner_worker.active_opportunities.items()):
                if t not in seen_opps:
                    events.append({"type": "OPPORTUNITY_ALERT", "ticker": t, "opportunity": opp})
            seen_opps = set(scanner_worker.active_opportunities.keys())

            if len(scanner_worker.execution_history) > seen_exec:
                for ex in scanner_worker.execution_history[: len(scanner_worker.execution_history) - seen_exec]:
                    events.append({"type": "ORDER_EXECUTED", "execution": ex})
            seen_exec = len(scanner_worker.execution_history)

            if was_running and not scanner_worker.running and "CIRCUIT_BREAKER" in str(scanner_worker.last_stop_reason):
                events.append({"type": "CIRCUIT_BREAKER_HALT", "reason": scanner_worker.last_stop_reason})
            was_running = scanner_worker.running

            audit_id = scanner_worker.last_audit_report.get("audit_id") if scanner_worker.last_audit_report else None
            if audit_id and audit_id != seen_audit:
                events.append({"type": "SESSION_AUDIT_READY", "audit_id": audit_id})
            seen_audit = audit_id

            curr_macro = _MACRO_REGIME_STATE["last_regime"]
            if curr_macro.get("status") != seen_macro_status:
                events.append({"type": "MACRO_REGIME_UPDATE", "macro_regime": curr_macro})
                seen_macro_status = curr_macro.get("status")

            await ws.send_json(
                {
                    "type": "STATE_SNAPSHOT",
                    "status": scanner_worker.get_status(),
                    "macro_regime": curr_macro,
                    "opportunities": list(scanner_worker.active_opportunities.values()),
                    "market_watch": scanner_worker.market_watch,
                    "events": events,
                    "server_time": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    except Exception as exc:
        logger.debug(f"WebSocket /ws/live cerrado: {exc}")

# =====================================================================
# ASESOR IA (GEMINI)
# =====================================================================

@app.get("/ai/status")
def ai_advisor_status():
    cfg = ai_advisor.ai_config()
    return {
        "enabled": cfg["enabled"],
        "key_configured": bool(cfg["api_key"]),
        "model": cfg["model"],
        "use_search": cfg["use_search"],
        "timeout_seconds": cfg["timeout_seconds"],
        "gate_full_auto": audit_agent.strategy_params.get("ai_gate_full_auto", True),
        "gate_timeout_seconds": audit_agent.strategy_params.get("ai_gate_timeout_seconds", 6.0),
        "gate_fail_open": audit_agent.strategy_params.get("ai_gate_fail_open", True),
        "cached_tickers": list(scanner_worker.ai_cache.keys()),
        "pending_tickers": list(scanner_worker.ai_pending),
    }


@app.post("/scanner/opportunities/{ticker}/ai-advice")
async def refresh_ai_advice(ticker: str):
    """Pide (o repite) el dictamen del asesor IA para una oportunidad activa."""
    symbol = ticker.strip().upper()
    opp = scanner_worker.active_opportunities.get(symbol)
    if not opp:
        raise HTTPException(status_code=404, detail=f"No hay oportunidad activa para {symbol}.")
    advice = await scanner_worker.request_advice(opp, force=True)
    live = scanner_worker.active_opportunities.get(symbol)
    if live is not None:
        live["ai_advice"] = advice
    return advice