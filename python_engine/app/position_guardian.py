"""
Agente Revisor / Guardián Activo de Posiciones en Vivo (V51 · Escenario H4).

Misiones (100% automático cada ciclo en MANUAL y FULL_AUTO):
1. Salida Swing por Sobrecompra RSI(2)_1D >= 75 en ventana EOD (15:45-16:00 ET) cuando PnL neto > $4.00.
2. Salida por Tiempo (12 días naturales) en ventana EOD para evitar capital muerto.
3. Candado Breakeven+ defensivo al alcanzar +1.20R (sube SL a Entrada + $2 comisiones + $3 neto).
4. Sin cierres prematuros intradía (EARLY_ABORT desactivado) para dejar trabajar al SL estructural (1.8x ATR_1D).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from three_tricks_engine import compute_ema, compute_session_vwap

NY_TZ = ZoneInfo("America/New_York")


def extract_intraday_guardian_signals(
    df_5m: Optional[pd.DataFrame],
    df_15m: Optional[pd.DataFrame],
) -> dict[str, Any]:
    """Calcula métricas intradía frescas para el Guardián (VWAP, EMA9_5m, última vela 5m, RVOL)."""
    d_base = df_5m if (df_5m is not None and not df_5m.empty and len(df_5m) >= 10) else df_15m
    if d_base is None or d_base.empty or len(d_base) < 5:
        return {
            "has_intraday": False,
            "vwap": None,
            "ema9_5m": None,
            "last_close": None,
            "last_open": None,
            "last_low": None,
            "prev_low": None,
            "upper_wick_ratio": 0.0,
            "rvol_intraday": 1.0,
            "below_vwap": False,
            "below_ema9": False,
        }

    close = d_base["Close"]
    open_s = d_base["Open"] if "Open" in d_base.columns else close
    high = d_base["High"]
    low = d_base["Low"]
    vol = d_base["Volume"] if "Volume" in d_base.columns else pd.Series(0.0, index=d_base.index)

    vwap_src = df_15m if (df_15m is not None and not df_15m.empty) else d_base
    vwap = compute_session_vwap(vwap_src)
    ema9 = compute_ema(close, 9)

    c = float(close.iloc[-1])
    o = float(open_s.iloc[-1])
    h = float(high.iloc[-1])
    l = float(low.iloc[-1])
    prev_l = float(low.iloc[-2]) if len(low) >= 2 else l
    c_ema9 = float(ema9.iloc[-1])

    rng = max(0.0001, h - l)
    upper_wick = h - max(o, c)
    upper_wick_ratio = upper_wick / rng

    avg_vol = float(vol.iloc[-21:-1].mean()) if len(vol) >= 6 else float(vol.mean())
    curr_vol = float(vol.iloc[-1])
    rvol = (curr_vol / avg_vol) if avg_vol > 0 else 1.0

    return {
        "has_intraday": True,
        "vwap": round(vwap, 2),
        "ema9_5m": round(c_ema9, 2),
        "last_close": round(c, 2),
        "last_open": round(o, 2),
        "last_low": round(l, 2),
        "prev_low": round(prev_l, 2),
        "upper_wick_ratio": round(upper_wick_ratio, 2),
        "rvol_intraday": round(rvol, 2),
        "below_vwap": bool(vwap > 0 and c < vwap),
        "below_ema9": bool(c < c_ema9),
    }


def evaluate_position_guardian(
    *,
    symbol: str,
    qty: float,
    entry_price: float,
    current_price: float,
    current_sl: Optional[float],
    current_tp: Optional[float],
    peak_price: float,
    initial_risk_per_share: float,
    unrealized_pl: float,
    clv_daily: float,
    intraday_sig: dict[str, Any],
    params: dict[str, Any],
    now_utc: Optional[datetime] = None,
    rsi2_daily: Optional[float] = None,
    days_held: float = 0.0,
) -> dict[str, Any]:
    """
    Evalúa una posición abierta bajo las reglas H4:
    - action_type: "NONE" | "ADJUST_SL" | "CLOSE_IMMEDIATELY"
    """
    qty_eff = max(1.0, float(qty))
    risk_ps = max(0.01, float(initial_risk_per_share))
    curr_r = round((current_price - entry_price) / risk_ps, 2)
    mfe_ps = max(0.0, peak_price - entry_price)
    mfe_r = round(mfe_ps / risk_ps, 2)
    mfe_usd = round(mfe_ps * qty_eff, 2)

    commission_per_order = float(params.get("commission_per_order", 1.0))
    round_trip_comm = commission_per_order * 2.0
    be_cushion_usd = float(params.get("breakeven_net_cushion_usd", 3.0))
    be_trigger_r = float(params.get("breakeven_trigger_r", 1.20))
    rsi2_exit_thresh = float(params.get("rsi2_exit_overbought", 75.0))
    swing_min_net_usd = float(params.get("swing_rsi2_min_net_profit_usd", 4.0))
    max_hold_days = float(params.get("max_hold_days", 12))
    eod_window_mins = int(params.get("eod_close_window_minutes", 15))

    # Precio de Breakeven+ que cubre comisiones de ida y vuelta + colchón neto positivo ($3)
    be_plus_sl = round(entry_price + ((round_trip_comm + be_cushion_usd) / qty_eff), 2)

    # -----------------------------------------------------------------
    # 1. VENTANA FIN DE SESIÓN NY (15:45 - 16:00 ET): SALIDA SWING RSI(2) O TIEMPO 12D
    # -----------------------------------------------------------------
    dt_utc = now_utc or datetime.now(timezone.utc)
    dt_ny = dt_utc.astimezone(NY_TZ)
    mins_ny = dt_ny.hour * 60 + dt_ny.minute
    close_mins = 16 * 60
    in_eod_window = (dt_ny.weekday() < 5) and ((close_mins - eod_window_mins) <= mins_ny < close_mins)

    if in_eod_window:
        net_unrealized = unrealized_pl - round_trip_comm
        if rsi2_daily is not None and rsi2_daily >= rsi2_exit_thresh and net_unrealized > swing_min_net_usd:
            return {
                "action_type": "CLOSE_IMMEDIATELY",
                "action_required": True,
                "regime_code": "RSI2_OVERBOUGHT_EXIT",
                "urgency": "HIGH",
                "recommended_sl": current_sl,
                "recommended_tp": current_tp,
                "headline": f"Salida Swing RSI(2)={rsi2_daily:.1f} en {symbol} (Neto est. ${net_unrealized:+.2f})",
                "why_proposed": (
                    f"{symbol} alcanzó sobrecompra diaria RSI(2)={rsi2_daily:.1f} (>= {rsi2_exit_thresh:.0f}) "
                    f"en ventana de cierre NY con beneficio neto estimado de ${net_unrealized:+.2f}."
                ),
                "action_summary": f"Cerrar {symbol} al cierre para embolsar el rebote Swing.",
                "quantitative_evidence": [
                    f"Hora NY: {dt_ny.strftime('%H:%M')} ET · RSI(2)_1D: {rsi2_daily:.1f}",
                    f"PnL bruto: ${unrealized_pl:+.2f} · PnL neto est.: ${net_unrealized:+.2f} ({curr_r:+.2f}R)",
                ],
            }

        if days_held >= max_hold_days:
            return {
                "action_type": "CLOSE_IMMEDIATELY",
                "action_required": True,
                "regime_code": "TIME_EXIT_12D",
                "urgency": "MEDIUM",
                "recommended_sl": current_sl,
                "recommended_tp": current_tp,
                "headline": f"Salida por Tiempo ({days_held:.0f}d) en {symbol} ({curr_r:+.2f}R / ${unrealized_pl:+.2f})",
                "why_proposed": (
                    f"{symbol} lleva {days_held:.0f} días abierto (>= {max_hold_days:.0f}d) sin resolver hacia TP/SL. "
                    f"Se libera el capital para rotar hacia nuevas oportunidades PB10."
                ),
                "action_summary": f"Cerrar {symbol} por cumplimiento de horizonte temporal ({max_hold_days:.0f} días).",
                "quantitative_evidence": [
                    f"Días en cartera: {days_held:.0f}d (límite {max_hold_days:.0f}d)",
                    f"Múltiplo actual: {curr_r:+.2f}R · PnL bruto: ${unrealized_pl:+.2f}",
                ],
            }

    # -----------------------------------------------------------------
    # 2. CANDADO DEFENSIVO BREAKEVEN+ EN +1.20R (SIN CORTES PREMATUROS)
    # -----------------------------------------------------------------
    be_hit = bool(params.get("enable_breakeven_protection", True)) and (max(curr_r, mfe_r) >= be_trigger_r)

    regime_code = "HOLD_STEADY"
    headline = f"Posición {symbol} en vigilancia H4 ({curr_r:+.2f}R / ${unrealized_pl:+.2f})"
    why_proposed = f"{symbol} evoluciona dentro de su margen estructural (SL 1.8x ATR_1D, objetivo 2.0R o RSI(2)>=75)."
    evidence: list[str] = [
        f"Actual: ${current_price:.2f} ({curr_r:+.2f}R) | Pico MFE: ${peak_price:.2f} (+{mfe_r:.2f}R / +${mfe_usd:.2f})",
        f"Candado BE+ (+{be_trigger_r:.2f}R): ${be_plus_sl:.2f}"
        + (f" | RSI(2)_1D: {rsi2_daily:.1f}" if rsi2_daily is not None else ""),
    ]

    if be_hit:
        final_sl = round(max(current_sl or 0.0, be_plus_sl), 2)
        regime_code = "BREAKEVEN_LOCK"
        headline = f"Candado Breakeven+ Activo en {symbol} (+{max(curr_r, mfe_r):.2f}R): SL en ${final_sl:.2f}"
        why_proposed = (
            f"{symbol} superó +{be_trigger_r:.2f}R (Pico +{mfe_r:.2f}R / +${mfe_usd:.2f}). "
            f"El Stop Loss sube a ${final_sl:.2f} (Entrada + comisiones + ${be_cushion_usd:.0f} neto)."
        )
        if final_sl < current_price - 0.02 and (current_sl is None or (final_sl - current_sl) >= 0.05):
            return {
                "action_type": "ADJUST_SL",
                "action_required": True,
                "regime_code": regime_code,
                "urgency": "MEDIUM",
                "recommended_sl": final_sl,
                "recommended_tp": current_tp,
                "headline": headline,
                "why_proposed": why_proposed,
                "action_summary": f"Subir Stop Loss en Alpaca a ${final_sl:.2f} (candado Breakeven+).",
                "quantitative_evidence": evidence,
            }

    return {
        "action_type": "NONE",
        "action_required": False,
        "regime_code": regime_code,
        "urgency": "LOW",
        "recommended_sl": current_sl,
        "recommended_tp": current_tp,
        "headline": headline,
        "why_proposed": why_proposed,
        "action_summary": "Mantener niveles OCO actuales sin cambios.",
        "quantitative_evidence": evidence,
    }
