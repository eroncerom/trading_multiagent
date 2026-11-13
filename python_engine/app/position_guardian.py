"""
Agente Revisor / Guardián Activo de Posiciones en Vivo.

Misiones (100% automático cada ciclo en MANUAL y FULL_AUTO):
1. Corte Temprano ('Perder 2 en vez de 14'): cierra al instante si tras entrar rompe VWAP con volumen vendedor.
2. Candado Breakeven+ inmediato (desde +$8 o +0.60R): sube SL sobre Entrada + Comisiones + Colchón.
3. Trailing Ratchet progresivo: asegura 60% del MFE desde +1.0R / +$12 y 75% desde +1.4R / +$18,
   y cierra por agotamiento si pierde EMA9_5m con rechazo.
4. Filtro Fin de Sesión (últimos 15 min NY, 15:45-16:00 ET): cierra posiciones salvo prórroga Swing (>= +1.5R y cierre fuerte).
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
) -> dict[str, Any]:
    """
    Evalúa una posición abierta y decide la acción exacta del Guardián:
    - action_type: "NONE" | "ADJUST_SL" | "CLOSE_IMMEDIATELY"
    - regime_code, urgency, headline, why_proposed, action_summary, quantitative_evidence
    - recommended_sl, recommended_tp
    """
    qty_eff = max(1.0, float(qty))
    risk_ps = max(0.01, float(initial_risk_per_share))
    curr_r = round((current_price - entry_price) / risk_ps, 2)
    mfe_ps = max(0.0, peak_price - entry_price)
    mfe_r = round(mfe_ps / risk_ps, 2)
    mfe_usd = round(mfe_ps * qty_eff, 2)

    commission_per_order = float(params.get("commission_per_order", 1.0))
    round_trip_comm = commission_per_order * 2.0
    be_cushion_usd = float(params.get("breakeven_net_cushion_usd", 2.0))
    be_trigger_r = float(params.get("breakeven_trigger_r", 0.60))
    be_trigger_usd = float(params.get("breakeven_trigger_usd", 8.0))

    trail1_r = float(params.get("trailing_stage1_trigger_r", 1.00))
    trail1_usd = float(params.get("trailing_stage1_trigger_usd", 12.0))
    trail1_mfe_pct = float(params.get("trailing_stage1_lock_mfe_pct", 0.60))

    trail2_r = float(params.get("trailing_stage2_trigger_r", 1.40))
    trail2_usd = float(params.get("trailing_stage2_trigger_usd", 18.0))
    trail2_mfe_pct = float(params.get("trailing_stage2_lock_mfe_pct", 0.75))

    swing_min_r = float(params.get("swing_overnight_min_r", 1.50))
    swing_min_clv = float(params.get("swing_overnight_min_clv", 0.70))
    eod_window_mins = int(params.get("eod_close_window_minutes", 15))

    # Precio de Breakeven+ que cubre comisiones de ida y vuelta + colchón neto positivo
    be_plus_sl = round(entry_price + ((round_trip_comm + be_cushion_usd) / qty_eff), 2)

    # -----------------------------------------------------------------
    # 1. REGLA FIN DE SESIÓN VS PRÓRROGA SWING (15:45 - 16:00 ET)
    # -----------------------------------------------------------------
    dt_utc = now_utc or datetime.now(timezone.utc)
    dt_ny = dt_utc.astimezone(NY_TZ)
    mins_ny = dt_ny.hour * 60 + dt_ny.minute
    close_mins = 16 * 60  # 16:00 ET cierre regular NYSE/NASDAQ
    in_eod_window = (dt_ny.weekday() < 5) and ((close_mins - eod_window_mins) <= mins_ny < close_mins)

    if in_eod_window:
        swing_ok = (
            curr_r >= swing_min_r
            and clv_daily >= swing_min_clv
            and (current_sl is not None and current_sl >= be_plus_sl)
        )
        if not swing_ok:
            return {
                "action_type": "CLOSE_IMMEDIATELY",
                "action_required": True,
                "regime_code": "EOD_CLOSE_POSITION",
                "urgency": "HIGH",
                "recommended_sl": current_sl,
                "recommended_tp": current_tp,
                "headline": f"Cierre Intradía Fin de Sesión en {symbol} ({curr_r:+.2f}R / ${unrealized_pl:+.2f})",
                "why_proposed": (
                    f"Faltan <= {eod_window_mins} min para el cierre de NY y {symbol} no cumple el colchón mínimo "
                    f"para prórroga Swing (>= +{swing_min_r:.2f}R y CLV >= {swing_min_clv:.2f}). Se cierra para evitar gaps nocturnos."
                ),
                "action_summary": f"Cerrar posición en {symbol} antes del cierre regular para evitar riesgo overnight.",
                "quantitative_evidence": [
                    f"Hora NY: {dt_ny.strftime('%H:%M')} ET (ventana de cierre)",
                    f"Múltiplo actual: {curr_r:+.2f}R (mínimo Swing: +{swing_min_r:.2f}R) · CLV 1D: {clv_daily:.2f}",
                ],
            }

    # -----------------------------------------------------------------
    # 2. CORTE TEMPRANO DE DAÑOS ('PERDER 2 EN VEZ DE 14')
    # -----------------------------------------------------------------
    if (
        intraday_sig.get("has_intraday")
        and unrealized_pl < 0.0
        and current_price < entry_price
        and intraday_sig.get("below_vwap")
        and float(intraday_sig.get("rvol_intraday") or 1.0) >= 1.30
        and float(intraday_sig.get("last_close") or current_price) < float(intraday_sig.get("last_open") or current_price)
    ):
        return {
            "action_type": "CLOSE_IMMEDIATELY",
            "action_required": True,
            "regime_code": "EARLY_ABORT_BY_REVIEWER",
            "urgency": "HIGH",
            "recommended_sl": current_sl,
            "recommended_tp": current_tp,
            "headline": f"Corte Temprano en {symbol}: pérdida de VWAP con volumen vendedor (${unrealized_pl:+.2f})",
            "why_proposed": (
                f"{symbol} perdió el VWAP (${intraday_sig['vwap']:.2f}) con vela bajista y RVOL {intraday_sig['rvol_intraday']:.2f}x. "
                f"El Agente Revisor corta el daño en ${unrealized_pl:+.2f} sin esperar a que caiga al Stop Loss."
            ),
            "action_summary": f"Cerrar {symbol} inmediatamente a mercado para limitar la pérdida.",
            "quantitative_evidence": [
                f"Precio ${current_price:.2f} < VWAP ${intraday_sig['vwap']:.2f}",
                f"Volumen vendedor RVOL {intraday_sig['rvol_intraday']:.2f}x >= 1.30x · PnL latente: ${unrealized_pl:+.2f}",
            ],
        }

    # -----------------------------------------------------------------
    # 3. SALIDA POR AGOTAMIENTO EN ZONA DE BENEFICIO (+1.0R / +$12)
    # -----------------------------------------------------------------
    in_profit_zone = (curr_r >= trail1_r) or (unrealized_pl >= trail1_usd)
    if (
        intraday_sig.get("has_intraday")
        and in_profit_zone
        and unrealized_pl > (round_trip_comm + 2.0)
        and intraday_sig.get("below_ema9")
        and (
            float(intraday_sig.get("upper_wick_ratio") or 0.0) >= 0.40
            or float(intraday_sig.get("last_close") or current_price) < float(intraday_sig.get("last_open") or current_price)
        )
    ):
        return {
            "action_type": "CLOSE_IMMEDIATELY",
            "action_required": True,
            "regime_code": "TAKE_PROFIT_EXHAUSTION_CLOSE",
            "urgency": "HIGH",
            "recommended_sl": current_sl,
            "recommended_tp": current_tp,
            "headline": f"Cierre por Agotamiento en {symbol}: embolsando ${unrealized_pl:+.2f}",
            "why_proposed": (
                f"{symbol} alcanzó zona de beneficio (+{max(curr_r, mfe_r):.2f}R) y perdió EMA9 intradía "
                f"(${intraday_sig['ema9_5m']:.2f}) con rechazo. Se asegura la ganancia antes de que retroceda."
            ),
            "action_summary": f"Cerrar {symbol} ahora y asegurar +${unrealized_pl:.2f} brutos en cuenta.",
            "quantitative_evidence": [
                f"PnL latente: ${unrealized_pl:+.2f} (Pico MFE: +${mfe_usd:.2f})",
                f"Cierre bajo EMA9_5m (${intraday_sig['ema9_5m']:.2f}) | Mecha superior {intraday_sig['upper_wick_ratio']*100:.0f}%",
            ],
        }

    # -----------------------------------------------------------------
    # 4. TRAILING RATCHET PROGRESIVO (75% MFE -> 60% MFE) Y BREAKEVEN+
    # -----------------------------------------------------------------
    stage2_hit = (max(curr_r, mfe_r) >= trail2_r) or (mfe_usd >= trail2_usd)
    stage1_hit = (max(curr_r, mfe_r) >= trail1_r) or (mfe_usd >= trail1_usd)
    be_hit = (max(curr_r, mfe_r) >= be_trigger_r) or (mfe_usd >= be_trigger_usd) or (unrealized_pl >= be_trigger_usd)

    target_sl: Optional[float] = None
    regime_code = "HOLD_STEADY"
    headline = f"Posición {symbol} dentro de márgenes ({curr_r:+.2f}R / ${unrealized_pl:+.2f})"
    why_proposed = f"{symbol} evoluciona correctamente sin alertas de giro ni umbrales pendientes."
    evidence: list[str] = [
        f"Actual: ${current_price:.2f} ({curr_r:+.2f}R) | Pico MFE: ${peak_price:.2f} (+{mfe_r:.2f}R / +${mfe_usd:.2f})",
        f"Suelo Breakeven+ (costes +${be_cushion_usd:.0f}): ${be_plus_sl:.2f}",
    ]

    if stage2_hit:
        lock_px = entry_price + (trail2_mfe_pct * mfe_ps)
        candle_floor = float(intraday_sig.get("prev_low") or 0.0)
        if candle_floor > be_plus_sl and candle_floor < current_price - 0.05:
            lock_px = max(lock_px, candle_floor - 0.02)
        target_sl = round(max(be_plus_sl, lock_px), 2)
        regime_code = "TRAILING_RATCHET_75"
        headline = f"Trailing Escudo 75% en {symbol}: SL protegido en ${target_sl:.2f}"
        why_proposed = (
            f"{symbol} alcanzó un pico de +${mfe_usd:.2f} (+{mfe_r:.2f}R). Se eleva el Stop Loss asegurando el 75% del tramo "
            f"favorable (${target_sl:.2f}) para evitar devolver ganancias."
        )
    elif stage1_hit:
        lock_px = entry_price + (trail1_mfe_pct * mfe_ps)
        target_sl = round(max(be_plus_sl, lock_px), 2)
        regime_code = "TRAILING_RATCHET_60"
        headline = f"Trailing Escudo 60% en {symbol}: SL protegido en ${target_sl:.2f}"
        why_proposed = (
            f"{symbol} superó +${trail1_usd:.0f} / +{trail1_r:.2f}R (Pico +${mfe_usd:.2f}). "
            f"El Guardián bloquea al menos el 60% del beneficio alcanzado subiendo el Stop a ${target_sl:.2f}."
        )
    elif be_hit:
        target_sl = be_plus_sl
        regime_code = "BREAKEVEN_LOCK"
        headline = f"Candado Breakeven+ Activo en {symbol}: imposible perder (${target_sl:.2f})"
        why_proposed = (
            f"{symbol} alcanzó el umbral de supervivencia (+${be_trigger_usd:.0f} o +{be_trigger_r:.2f}R). "
            f"El Stop Loss sube a ${target_sl:.2f} (Entrada + comisiones + ${be_cushion_usd:.0f} neto)."
        )

    if target_sl is not None:
        # Si ya tocó zona ganadora pero el precio actual cayó por debajo del candado que correspondía, cerrar para no volver a rojo
        if current_price <= target_sl + 0.02:
            if current_price > entry_price + (round_trip_comm / qty_eff):
                return {
                    "action_type": "CLOSE_IMMEDIATELY",
                    "action_required": True,
                    "regime_code": "PROFIT_LOCK_MARKET_CLOSE",
                    "urgency": "HIGH",
                    "recommended_sl": current_sl,
                    "recommended_tp": current_tp,
                    "headline": f"Asegurando Ganancia en {symbol}: retroceso hacia suelo protegido (${current_price:.2f})",
                    "why_proposed": (
                        f"{symbol} alcanzó +${mfe_usd:.2f} y ha retrocedido hasta el nivel protegido (${target_sl:.2f}). "
                        "Se cierra al instante para garantizar beneficio neto positivo."
                    ),
                    "action_summary": f"Cerrar {symbol} para asegurar +${unrealized_pl:.2f} antes de que siga cayendo.",
                    "quantitative_evidence": evidence,
                }
            # Si aún no está bajo entrada pero sí muy pegado, ajustamos justo debajo del precio si sigue sobre breakeven
            target_sl = round(max(be_plus_sl, current_price - 0.05), 2)

        final_sl = round(max(current_sl or 0.0, target_sl), 2)
        if final_sl < current_price - 0.02 and (current_sl is None or (final_sl - current_sl) >= 0.05):
            return {
                "action_type": "ADJUST_SL",
                "action_required": True,
                "regime_code": regime_code,
                "urgency": "HIGH" if stage1_hit else "MEDIUM",
                "recommended_sl": final_sl,
                "recommended_tp": current_tp,
                "headline": headline,
                "why_proposed": why_proposed,
                "action_summary": f"Subir Stop Loss en Alpaca a ${final_sl:.2f} (nunca retrocede).",
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
