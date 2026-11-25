"""
Agente de Evaluación Técnica Defensiva y Puerta Dual (Fundamental OK + Técnico OK).

Combina:
1. Estructura de tendencia y régimen en velas diarias (1D): medias móviles, ADX/+DI/-DI, RSI, anti-desplome.
2. Reconocimiento determinista de figuras chartistas alcistas (chart_patterns_engine.py).
3. Confirmación táctica en velas horarias (1H) para afinar la entrada sin ruido.
4. Regla estricta AND (fail-closed): si falla el Agente Fundamental (ai_advisor) o el Agente Técnico,
   la entrada se bloquea automáticamente.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from chart_patterns_engine import analyze_chart_patterns

VALID_TECHNICAL_VERDICTS = ("APPROVE", "WATCHLIST", "REJECT")


def evaluate_technical_gate(
    daily_struct: Optional[dict[str, Any]],
    df_daily: Optional[pd.DataFrame],
    df_1h: Optional[pd.DataFrame] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Evalúa si el momento técnico actual es apto para entrar bajo perfil defensivo.
    Devuelve veredicto estructurado: APPROVE | WATCHLIST | REJECT.
    """
    cfg = params or {}
    if daily_struct is None or df_daily is None or df_daily.empty:
        return {
            "status": "UNAVAILABLE",
            "verdict": "REJECT",
            "confidence": 0,
            "headline": "Datos técnicos 1D insuficientes (bloqueo fail-closed).",
            "trend_ok": False,
            "pattern_status": "NONE",
            "best_pattern": None,
            "hourly_confirmation": {"available": False, "confirmed": False, "reason": "Sin datos."},
            "checks": [],
            "reasons": ["Faltan velas diarias (1D) para evaluar estructura técnica."],
        }

    price = float(daily_struct.get("price", 0.0))
    sma_200 = float(daily_struct.get("sma_200", 0.0))
    ema_20 = float(daily_struct.get("ema_20", 0.0))
    sma_50 = float(daily_struct.get("sma_50", 0.0))
    plus_di = float(daily_struct.get("plus_di", 0.0))
    minus_di = float(daily_struct.get("minus_di", 0.0))
    rsi_14 = float(daily_struct.get("rsi_14", 50.0))
    falling_knife = bool(daily_struct.get("falling_knife", False))
    daily_trend_ok = bool(daily_struct.get("daily_trend_ok", False))

    max_rsi = float(cfg.get("technical_max_rsi14", 72.0))
    require_confirmed_pattern = bool(cfg.get("technical_require_confirmed_pattern", False))
    require_hourly = bool(cfg.get("technical_require_hourly_confirmation", True))

    pattern_analysis = analyze_chart_patterns(df_daily, df_1h=df_1h, params=cfg)
    best_pattern = pattern_analysis.get("best_pattern")
    pattern_status = pattern_analysis.get("pattern_status", "NONE")
    hourly = pattern_analysis.get("hourly_confirmation") or {"available": False, "confirmed": False}

    # Comprobaciones defensivas obligatorias
    no_falling_knife = not falling_knife
    not_overbought = rsi_14 <= max_rsi
    di_constructive = (plus_di >= minus_di) or (pattern_status == "CONFIRMED")

    # Evitar comprar justo debajo de una SMA200 actuando como techo inmediato (< 3% arriba)
    near_sma200_ceiling = (
        sma_200 > 0
        and price < sma_200
        and ((sma_200 - price) / max(0.01, price)) < 0.03
    )
    clear_of_sma200_trap = not near_sma200_ceiling

    checks = [
        {
            "label": "Sin caída libre (Anti-Cuchillo)",
            "passed": no_falling_knife,
            "detail": f"Retorno 1D {daily_struct.get('ret_1d_pct', 0.0):+.2f}%",
        },
        {
            "label": "Flujo direccional constructivo (+DI >= -DI o ruptura)",
            "passed": di_constructive,
            "detail": f"+DI {plus_di:.1f} vs -DI {minus_di:.1f}",
        },
        {
            "label": f"Sin sobrecompra extrema (RSI14 <= {max_rsi:.0f})",
            "passed": not_overbought,
            "detail": f"RSI(14)={rsi_14:.1f}",
        },
        {
            "label": "Sin techo inmediato en SMA200",
            "passed": clear_of_sma200_trap,
            "detail": f"Precio ${price:.2f} vs SMA200 ${sma_200:.2f}",
        },
    ]

    reasons: list[str] = []
    verdict = "REJECT"
    confidence = 40

    if not no_falling_knife:
        verdict = "REJECT"
        confidence = 90
        reasons.append("Caída diaria severa (falling knife): entrada bloqueada.")
    elif not not_overbought:
        verdict = "REJECT"
        confidence = 80
        reasons.append(f"RSI(14)={rsi_14:.1f} en zona de sobrecompra (> {max_rsi:.0f}).")
    elif not clear_of_sma200_trap and pattern_status != "CONFIRMED":
        verdict = "WATCHLIST"
        confidence = 65
        reasons.append(f"Precio ${price:.2f} pegado bajo resistencia SMA200 (${sma_200:.2f}).")
    elif not di_constructive:
        if pattern_status == "FORMING":
            verdict = "WATCHLIST"
            confidence = 60
            reasons.append(f"Figura {best_pattern['label']} en formación; flujo todavía bajista (-DI > +DI).")
        else:
            verdict = "REJECT"
            confidence = 75
            reasons.append(f"Tendencia bajista activa (-DI {minus_di:.1f} > +DI {plus_di:.1f}) sin figura de giro.")
    else:
        # Base defensiva sana: evaluar patrón + tendencia + confirmación 1H
        if pattern_status == "CONFIRMED" and best_pattern is not None:
            if require_hourly and hourly.get("available") and not hourly.get("confirmed"):
                verdict = "WATCHLIST"
                confidence = int(best_pattern.get("confidence", 70))
                reasons.append(f"{best_pattern['label']} roto en 1D, esperando confirmación horaria (1H).")
            else:
                verdict = "APPROVE"
                confidence = int(best_pattern.get("confidence", 82))
                reasons.append(
                    f"{best_pattern['label']} confirmado (neckline ${best_pattern['neckline']:.2f}, "
                    f"objetivo ${best_pattern['projected_target']:.2f}, RVOL {best_pattern['breakout_rvol']:.2f}x)."
                )
        elif pattern_status == "FORMING" and best_pattern is not None:
            verdict = "WATCHLIST"
            confidence = int(best_pattern.get("confidence", 60))
            reasons.append(best_pattern.get("detail") or f"{best_pattern['label']} en formación esperando ruptura.")
        elif daily_trend_ok and not require_confirmed_pattern:
            if require_hourly and hourly.get("available") and not hourly.get("confirmed"):
                verdict = "WATCHLIST"
                confidence = 62
                reasons.append("Tendencia 1D alcista sana, esperando confirmación horaria (1H).")
            else:
                verdict = "APPROVE"
                confidence = 74
                reasons.append(
                    f"Tendencia alcista 1D confirmada (Precio ${price:.2f} > EMA20 ${ema_20:.2f} > SMA50 ${sma_50:.2f} > SMA200 ${sma_200:.2f})."
                )
        else:
            verdict = "WATCHLIST" if daily_trend_ok else "REJECT"
            confidence = 55
            reasons.append(
                "Esperando figura chartista confirmada con volumen."
                if require_confirmed_pattern
                else "Tendencia 1D incompleta sin figura de cambio confirmada."
            )

    headline = reasons[0] if reasons else f"Dictamen técnico: {verdict}."
    return {
        "status": "DONE",
        "verdict": verdict,
        "confidence": confidence,
        "headline": headline,
        "trend_ok": bool(daily_trend_ok),
        "pattern_status": pattern_status,
        "best_pattern": best_pattern,
        "hourly_confirmation": hourly,
        "checks": checks,
        "reasons": reasons,
    }


def evaluate_dual_gate(
    ai_advice: Optional[dict[str, Any]],
    technical_eval: Optional[dict[str, Any]],
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Puerta AND estricta (perfil defensivo fail-closed):
    Solo autoriza entrada si TANTO el Agente Fundamental (ai_advice) COMO el Agente Técnico
    devuelven veredicto APPROVE. Cualquier CAUTION, WATCHLIST, REJECT o fallo bloquea la entrada.
    """
    cfg = params or {}
    fail_closed = bool(cfg.get("dual_gate_fail_closed", True))

    fund_status = (ai_advice or {}).get("status")
    fund_verdict = (ai_advice or {}).get("verdict")
    fundamental_ok = bool(fund_status == "DONE" and fund_verdict == "APPROVE")

    tech_status = (technical_eval or {}).get("status")
    tech_verdict = (technical_eval or {}).get("verdict")
    technical_ok = bool(tech_status == "DONE" and tech_verdict == "APPROVE")

    if not fail_closed:
        # Si alguna vez se desactiva fail_closed explícitamente, permitir si solo falta disponibilidad
        if fund_status != "DONE" and technical_ok:
            fundamental_ok = True

    passed = bool(fundamental_ok and technical_ok)

    if passed:
        gate_status = "APPROVED"
        reason = "Doble confirmación OK: Agente Fundamental (APPROVE) y Agente Técnico (APPROVE)."
    elif not fundamental_ok and not technical_ok:
        gate_status = "BLOCKED_BOTH"
        reason = (
            f"Bloqueado por ambos agentes (Fundamental={fund_verdict or fund_status or 'MISSING'}, "
            f"Técnico={tech_verdict or tech_status or 'MISSING'})."
        )
    elif not fundamental_ok:
        gate_status = "BLOCKED_FUNDAMENTAL"
        reason = (
            f"Bloqueado por Agente Fundamental ({fund_verdict or fund_status or 'UNAVAILABLE'}): "
            f"{(ai_advice or {}).get('headline') or (ai_advice or {}).get('reason') or 'sin aprobación fundamental'}."
        )
    else:
        gate_status = "WATCHLIST_TECHNICAL" if tech_verdict == "WATCHLIST" else "BLOCKED_TECHNICAL"
        reason = (
            f"Bloqueado por Agente Técnico ({tech_verdict or tech_status or 'UNAVAILABLE'}): "
            f"{(technical_eval or {}).get('headline') or 'esperando confirmación técnica'}."
        )

    return {
        "passed": passed,
        "status": gate_status,
        "fail_closed": fail_closed,
        "fundamental_ok": fundamental_ok,
        "fundamental_verdict": fund_verdict or fund_status or "PENDING",
        "technical_ok": technical_ok,
        "technical_verdict": tech_verdict or tech_status or "UNAVAILABLE",
        "reason": reason,
    }
