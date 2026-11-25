"""
Motor Determinista de Reconocimiento de Figuras Chartistas (Defensivo).

Detecta patrones alcistas sobre velas diarias (1D) con confirmación opcional en velas horarias (1H):
- Hombro-Cabeza-Hombro Invertido (INVERSE_HEAD_AND_SHOULDERS)
- Doble / Triple Suelo (DOUBLE_BOTTOM / TRIPLE_BOTTOM)
- Ruptura de Rango / Consolidación (CONSOLIDATION_BREAKOUT)

Principios:
- 100% determinista en pandas/numpy (sin dependencia de LLM ni coste por llamada).
- Tolerancias configurables orientadas a perfil defensivo (simetría estricta, ventana mínima/máxima,
  cierre confirmado sobre neckline y volumen relativo de confirmación).
- Calcula automáticamente proyección de objetivo y nivel técnico de invalidación (stop-loss estructural).
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from three_tricks_engine import compute_ema, compute_rsi

DEFAULT_PATTERN_CONFIG: dict[str, Any] = {
    "pivot_window": 3,
    "min_pattern_bars": 15,
    "max_pattern_bars": 120,
    "max_bottom_diff_pct": 0.025,       # <= 2.5% diferencia entre suelos
    "min_bottom_rebound_pct": 0.05,     # >= 5.0% rebote intermedio hacia neckline
    "max_shoulder_diff_pct": 0.03,      # <= 3.0% diferencia entre hombro izq y der
    "min_head_depth_pct": 0.05,         # cabeza >= 5.0% por debajo de hombros
    "max_neckline_slope_pct": 0.15,     # evita necklines distorsionadas
    "breakout_confirm_pct": 0.01,       # cierre >= +1.0% sobre neckline
    "breakout_recency_bars": 5,         # ruptura ocurrida en las últimas 5 sesiones
    "min_breakout_rvol": 1.30,          # volumen >= 1.3x media 20 sesiones
    "watchlist_proximity_pct": 0.04,    # a <= 4% por debajo de neckline -> FORMING
    "range_min_bars": 15,
    "range_max_bars": 60,
    "range_max_height_pct": 0.12,       # caja lateral estrecha <= 12%
}


def _merge_cfg(params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_PATTERN_CONFIG)
    if params:
        for k in DEFAULT_PATTERN_CONFIG:
            if k in params and params[k] is not None:
                cfg[k] = params[k]
    return cfg


def _clean_ohlcv(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in out.columns for c in required):
        return pd.DataFrame()
    out = out[required].apply(pd.to_numeric, errors="coerce").dropna()
    return out


def find_swing_pivots(
    high: np.ndarray,
    low: np.ndarray,
    window: int = 3,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Identifica pivotes locales (Swing Highs y Swing Lows) sin mirar al futuro más allá de `window`."""
    n = len(high)
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    if n < (2 * window + 1):
        return swing_highs, swing_lows

    for i in range(window, n - window):
        h_slice = high[i - window : i + window + 1]
        l_slice = low[i - window : i + window + 1]
        if high[i] == np.max(h_slice) and np.sum(h_slice == high[i]) == 1:
            swing_highs.append((i, float(high[i])))
        elif high[i] == np.max(h_slice) and high[i] > high[i - 1]:
            swing_highs.append((i, float(high[i])))

        if low[i] == np.min(l_slice) and np.sum(l_slice == low[i]) == 1:
            swing_lows.append((i, float(low[i])))
        elif low[i] == np.min(l_slice) and low[i] < low[i - 1]:
            swing_lows.append((i, float(low[i])))

    return swing_highs, swing_lows


def _compute_recent_rvol(volume: np.ndarray, idx: int, lookback: int = 20) -> float:
    start = max(0, idx - lookback)
    if idx <= start:
        return 1.0
    avg_vol = float(np.mean(volume[start:idx]))
    if avg_vol <= 0:
        return 1.0
    return float(volume[idx] / avg_vol)


def _evaluate_breakout_status(
    closes: np.ndarray,
    volumes: np.ndarray,
    neckline_fn,
    start_check_idx: int,
    cfg: dict[str, Any],
) -> tuple[str, float, Optional[int], str]:
    """
    Evalúa si el precio ha confirmado ruptura defensiva sobre la neckline o está en formación.
    Devuelve (status, breakout_rvol, breakout_bar_idx, detail).
    """
    n = len(closes)
    last_idx = n - 1
    curr_close = float(closes[last_idx])
    neck_now = float(neckline_fn(last_idx))
    confirm_pct = float(cfg["breakout_confirm_pct"])
    min_rvol = float(cfg["min_breakout_rvol"])
    recency = int(cfg["breakout_recency_bars"])
    prox_pct = float(cfg["watchlist_proximity_pct"])

    # Buscar si la ruptura ocurrió en las últimas `recency` sesiones
    search_from = max(start_check_idx + 1, n - recency)
    breakout_idx: Optional[int] = None
    best_rvol = _compute_recent_rvol(volumes, last_idx)

    for i in range(search_from, n):
        neck_i = float(neckline_fn(i))
        prev_close = float(closes[i - 1]) if i > 0 else float(closes[i])
        prev_neck = float(neckline_fn(i - 1)) if i > 0 else neck_i
        if closes[i] >= neck_i * (1.0 + confirm_pct) and prev_close < prev_neck * (1.0 + confirm_pct):
            breakout_idx = i
            best_rvol = max(best_rvol, _compute_recent_rvol(volumes, i))

    # También contemplar si hoy cerró claramente por encima y ayer estaba por debajo de la neckline
    if breakout_idx is None and curr_close >= neck_now * (1.0 + confirm_pct):
        # Verificar que no lleva ya > recency barras muy por encima (ruptura vieja)
        pre_window_start = max(start_check_idx, n - recency - 1)
        was_below_recently = any(
            closes[j] < float(neckline_fn(j)) * (1.0 + confirm_pct)
            for j in range(pre_window_start, last_idx)
        )
        if was_below_recently:
            breakout_idx = last_idx
            best_rvol = _compute_recent_rvol(volumes, last_idx)

    if breakout_idx is not None and curr_close >= neck_now * (1.0 + confirm_pct * 0.5):
        if best_rvol >= min_rvol:
            return (
                "CONFIRMED",
                round(best_rvol, 2),
                breakout_idx,
                f"Ruptura confirmada sobre neckline ${neck_now:.2f} con RVOL {best_rvol:.2f}x.",
            )
        return (
            "FORMING",
            round(best_rvol, 2),
            breakout_idx,
            f"Ruptura de neckline ${neck_now:.2f} sin volumen suficiente (RVOL {best_rvol:.2f}x < {min_rvol:.2f}x).",
        )

    if neck_now * (1.0 - prox_pct) <= curr_close < neck_now * (1.0 + confirm_pct):
        dist_pct = ((neck_now - curr_close) / max(0.01, neck_now)) * 100.0
        return (
            "FORMING",
            round(best_rvol, 2),
            None,
            f"En formación: precio ${curr_close:.2f} a {dist_pct:.1f}% de la neckline ${neck_now:.2f}.",
        )

    return "NONE", round(best_rvol, 2), None, ""


def detect_inverse_head_and_shoulders(
    df_daily: pd.DataFrame,
    params: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Detecta Hombro-Cabeza-Hombro Invertido (HCHi) alcista con tolerancias defensivas."""
    cfg = _merge_cfg(params)
    df = _clean_ohlcv(df_daily)
    max_bars = int(cfg["max_pattern_bars"])
    min_bars = int(cfg["min_pattern_bars"])
    if df.empty or len(df) < min_bars + 10:
        return None

    sub = df.tail(max_bars + 25).reset_index(drop=True)
    highs = sub["High"].to_numpy(dtype=float)
    lows = sub["Low"].to_numpy(dtype=float)
    closes = sub["Close"].to_numpy(dtype=float)
    volumes = sub["Volume"].to_numpy(dtype=float)

    _, swing_lows = find_swing_pivots(highs, lows, window=int(cfg["pivot_window"]))
    if len(swing_lows) < 3:
        return None

    max_sh_diff = float(cfg["max_shoulder_diff_pct"])
    min_head_depth = float(cfg["min_head_depth_pct"])
    max_neck_slope = float(cfg["max_neckline_slope_pct"])
    min_rebound = float(cfg["min_bottom_rebound_pct"])

    best_match: Optional[dict[str, Any]] = None
    best_rank = (-1, -1.0)

    # Evaluar combinaciones recientes (LS, Head, RS)
    recent_lows = swing_lows[-10:]
    m = len(recent_lows)
    for k in range(m - 1, 1, -1):
        rs_idx, rs_px = recent_lows[k]
        # El hombro derecho no puede ser demasiado antiguo
        if (len(sub) - 1 - rs_idx) > 25:
            continue
        for j in range(k - 1, 0, -1):
            head_idx, head_px = recent_lows[j]
            if head_px >= rs_px * (1.0 - min_head_depth):
                continue
            for i in range(j - 1, -1, -1):
                ls_idx, ls_px = recent_lows[i]
                span = rs_idx - ls_idx
                if span < min_bars or span > max_bars:
                    continue
                if head_px >= ls_px * (1.0 - min_head_depth):
                    continue
                sh_diff = abs(ls_px - rs_px) / max(0.01, min(ls_px, rs_px))
                if sh_diff > max_sh_diff:
                    continue

                # Picos que forman la neckline entre LS-Head y Head-RS
                if head_idx <= ls_idx + 1 or rs_idx <= head_idx + 1:
                    continue
                p1_rel = int(np.argmax(highs[ls_idx : head_idx + 1]))
                p1_idx = ls_idx + p1_rel
                p1_px = float(highs[p1_idx])

                p2_rel = int(np.argmax(highs[head_idx : rs_idx + 1]))
                p2_idx = head_idx + p2_rel
                p2_px = float(highs[p2_idx])

                if p2_idx <= p1_idx:
                    continue
                neck_slope_pct = abs(p2_px - p1_px) / max(0.01, p1_px)
                if neck_slope_pct > max_neck_slope:
                    continue

                slope_per_bar = (p2_px - p1_px) / float(p2_idx - p1_idx)

                def _neck_at(bar_i: int, _p1_idx=p1_idx, _p1_px=p1_px, _s=slope_per_bar) -> float:
                    return _p1_px + _s * (bar_i - _p1_idx)

                neck_head = _neck_at(head_idx)
                height = neck_head - head_px
                if height <= 0 or (height / max(0.01, head_px)) < min_rebound:
                    continue

                status, rvol, brk_idx, detail = _evaluate_breakout_status(
                    closes, volumes, _neck_at, rs_idx, cfg
                )
                if status == "NONE":
                    continue

                neck_now = _neck_at(len(sub) - 1)
                target_px = round(neck_now + height, 2)
                invalidation_px = round(rs_px * 0.99, 2)

                # Confianza 0-100 basada en simetría, profundidad de cabeza y volumen
                symmetry_score = max(0.0, 1.0 - (sh_diff / max_sh_diff)) * 30.0
                depth_score = min(1.0, ((min(ls_px, rs_px) - head_px) / head_px) / 0.12) * 25.0
                vol_score = min(1.0, rvol / float(cfg["min_breakout_rvol"])) * 25.0
                status_bonus = 20.0 if status == "CONFIRMED" else 5.0
                confidence = int(round(min(99.0, symmetry_score + depth_score + vol_score + status_bonus)))

                rank = (2 if status == "CONFIRMED" else 1, float(confidence))
                if rank > best_rank:
                    best_rank = rank
                    best_match = {
                        "pattern": "INVERSE_HEAD_AND_SHOULDERS",
                        "label": "Hombro-Cabeza-Hombro Invertido",
                        "status": status,
                        "confidence": confidence,
                        "neckline": round(neck_now, 2),
                        "projected_target": target_px,
                        "invalidation_price": invalidation_px,
                        "pattern_height_pct": round((height / max(0.01, head_px)) * 100.0, 2),
                        "span_bars": int(span),
                        "shoulder_diff_pct": round(sh_diff * 100.0, 2),
                        "breakout_rvol": rvol,
                        "breakout_bar_ago": (len(sub) - 1 - brk_idx) if brk_idx is not None else None,
                        "pivots": {
                            "left_shoulder": round(ls_px, 2),
                            "head": round(head_px, 2),
                            "right_shoulder": round(rs_px, 2),
                        },
                        "detail": detail,
                    }

    return best_match


def detect_double_or_triple_bottom(
    df_daily: pd.DataFrame,
    params: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Detecta Doble Suelo o Triple Suelo alcista con tolerancias defensivas."""
    cfg = _merge_cfg(params)
    df = _clean_ohlcv(df_daily)
    max_bars = int(cfg["max_pattern_bars"])
    min_bars = int(cfg["min_pattern_bars"])
    if df.empty or len(df) < min_bars + 10:
        return None

    sub = df.tail(max_bars + 25).reset_index(drop=True)
    highs = sub["High"].to_numpy(dtype=float)
    lows = sub["Low"].to_numpy(dtype=float)
    closes = sub["Close"].to_numpy(dtype=float)
    volumes = sub["Volume"].to_numpy(dtype=float)

    _, swing_lows = find_swing_pivots(highs, lows, window=int(cfg["pivot_window"]))
    if len(swing_lows) < 2:
        return None

    max_diff = float(cfg["max_bottom_diff_pct"])
    min_rebound = float(cfg["min_bottom_rebound_pct"])

    best_match: Optional[dict[str, Any]] = None
    best_rank = (-1, -1.0)

    recent_lows = swing_lows[-10:]
    m = len(recent_lows)
    for j in range(m - 1, 0, -1):
        b2_idx, b2_px = recent_lows[j]
        if (len(sub) - 1 - b2_idx) > 25:
            continue
        for i in range(j - 1, -1, -1):
            b1_idx, b1_px = recent_lows[i]
            span = b2_idx - b1_idx
            if span < min_bars or span > max_bars:
                continue

            diff_pct = abs(b1_px - b2_px) / max(0.01, min(b1_px, b2_px))
            if diff_pct > max_diff:
                continue

            # No debe haber una caída significativamente más profunda entre b1 y b2
            inter_min = float(np.min(lows[b1_idx : b2_idx + 1]))
            if inter_min < min(b1_px, b2_px) * (1.0 - max_diff):
                continue

            neck_px = float(np.max(highs[b1_idx : b2_idx + 1]))
            avg_bottom = (b1_px + b2_px) / 2.0
            height = neck_px - min(b1_px, b2_px)
            if (height / max(0.01, avg_bottom)) < min_rebound:
                continue

            # Comprobar si existe un tercer suelo intermedio o previo al mismo nivel (Triple Suelo)
            matched_bottoms = [(b1_idx, b1_px), (b2_idx, b2_px)]
            for t_idx, t_px in recent_lows:
                if t_idx in (b1_idx, b2_idx):
                    continue
                if b1_idx < t_idx < b2_idx:
                    if abs(t_px - avg_bottom) / max(0.01, avg_bottom) <= max_diff:
                        matched_bottoms.append((t_idx, t_px))
                        break

            is_triple = len(matched_bottoms) >= 3
            pattern_code = "TRIPLE_BOTTOM" if is_triple else "DOUBLE_BOTTOM"
            pattern_label = "Triple Suelo" if is_triple else "Doble Suelo"

            status, rvol, brk_idx, detail = _evaluate_breakout_status(
                closes, volumes, lambda _i, _n=neck_px: _n, b2_idx, cfg
            )
            if status == "NONE":
                continue

            min_b = min(px for _, px in matched_bottoms)
            target_px = round(neck_px + (neck_px - min_b), 2)
            invalidation_px = round(min_b * 0.99, 2)

            symmetry_score = max(0.0, 1.0 - (diff_pct / max_diff)) * 30.0
            rebound_score = min(1.0, (height / max(0.01, avg_bottom)) / 0.12) * 20.0
            vol_score = min(1.0, rvol / float(cfg["min_breakout_rvol"])) * 25.0
            triple_bonus = 5.0 if is_triple else 0.0
            status_bonus = 20.0 if status == "CONFIRMED" else 5.0
            confidence = int(round(min(99.0, symmetry_score + rebound_score + vol_score + triple_bonus + status_bonus)))

            rank = (2 if status == "CONFIRMED" else 1, float(confidence))
            if rank > best_rank:
                best_rank = rank
                best_match = {
                    "pattern": pattern_code,
                    "label": pattern_label,
                    "status": status,
                    "confidence": confidence,
                    "neckline": round(neck_px, 2),
                    "projected_target": target_px,
                    "invalidation_price": invalidation_px,
                    "pattern_height_pct": round((height / max(0.01, avg_bottom)) * 100.0, 2),
                    "span_bars": int(span),
                    "bottom_diff_pct": round(diff_pct * 100.0, 2),
                    "breakout_rvol": rvol,
                    "breakout_bar_ago": (len(sub) - 1 - brk_idx) if brk_idx is not None else None,
                    "pivots": {
                        "bottoms": [round(px, 2) for _, px in sorted(matched_bottoms, key=lambda x: x[0])],
                    },
                    "detail": detail,
                }

    return best_match


def detect_consolidation_breakout(
    df_daily: pd.DataFrame,
    params: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Detecta ruptura alcista defensiva de una caja de consolidación lateral estrecha con volumen."""
    cfg = _merge_cfg(params)
    df = _clean_ohlcv(df_daily)
    min_r = int(cfg["range_min_bars"])
    max_r = int(cfg["range_max_bars"])
    max_h_pct = float(cfg["range_max_height_pct"])
    if df.empty or len(df) < min_r + 5:
        return None

    sub = df.tail(max_r + 10).reset_index(drop=True)
    highs = sub["High"].to_numpy(dtype=float)
    lows = sub["Low"].to_numpy(dtype=float)
    closes = sub["Close"].to_numpy(dtype=float)
    volumes = sub["Volume"].to_numpy(dtype=float)
    n = len(sub)

    best_match: Optional[dict[str, Any]] = None
    best_rank = (-1, -1.0)

    for box_len in (20, 30, 45):
        if box_len < min_r or box_len > max_r or n < box_len + 2:
            continue
        box_end = n - 2  # excluir la vela actual para evaluar la ruptura
        box_start = max(0, box_end - box_len)
        box_high = float(np.max(highs[box_start:box_end]))
        box_low = float(np.min(lows[box_start:box_end]))
        if box_low <= 0:
            continue
        height = box_high - box_low
        height_pct = height / box_low
        if height_pct <= 0.02 or height_pct > max_h_pct:
            continue

        status, rvol, brk_idx, detail = _evaluate_breakout_status(
            closes, volumes, lambda _i, _n=box_high: _n, box_end - 1, cfg
        )
        if status == "NONE":
            continue

        target_px = round(box_high + height, 2)
        invalidation_px = round(box_low * 0.99, 2)
        tightness_score = max(0.0, 1.0 - (height_pct / max_h_pct)) * 35.0
        vol_score = min(1.0, rvol / float(cfg["min_breakout_rvol"])) * 35.0
        status_bonus = 25.0 if status == "CONFIRMED" else 5.0
        confidence = int(round(min(96.0, tightness_score + vol_score + status_bonus)))

        rank = (2 if status == "CONFIRMED" else 1, float(confidence))
        if rank > best_rank:
            best_rank = rank
            best_match = {
                "pattern": "CONSOLIDATION_BREAKOUT",
                "label": f"Ruptura de Rango ({box_len}d)",
                "status": status,
                "confidence": confidence,
                "neckline": round(box_high, 2),
                "projected_target": target_px,
                "invalidation_price": invalidation_px,
                "pattern_height_pct": round(height_pct * 100.0, 2),
                "span_bars": int(box_len),
                "breakout_rvol": rvol,
                "breakout_bar_ago": (n - 1 - brk_idx) if brk_idx is not None else None,
                "pivots": {
                    "box_low": round(box_low, 2),
                    "box_high": round(box_high, 2),
                },
                "detail": detail,
            }

    return best_match


def confirm_hourly_entry(
    df_1h: Optional[pd.DataFrame],
    neckline: Optional[float] = None,
) -> dict[str, Any]:
    """
    Validación táctica en velas horarias (1H):
    Confirma que el precio mantiene fuerza sobre EMA20_1H (y neckline si aplica)
    sin sobrecompra extrema ni rechazo bajista fuerte en la última vela horaria.
    """
    df = _clean_ohlcv(df_1h)
    if df.empty or len(df) < 20:
        return {
            "available": False,
            "confirmed": False,
            "reason": "Sin velas horarias (1H) suficientes para confirmar momento de entrada.",
        }

    close = df["Close"]
    open_ = df["Open"]
    high = df["High"]
    low = df["Low"]

    ema20 = compute_ema(close, 20)
    rsi14 = compute_rsi(close, 14)

    curr_close = float(close.iloc[-1])
    curr_open = float(open_.iloc[-1])
    curr_high = float(high.iloc[-1])
    curr_low = float(low.iloc[-1])
    c_ema20 = float(ema20.iloc[-1])
    c_rsi = float(rsi14.iloc[-1])

    rng = max(0.0001, curr_high - curr_low)
    upper_wick_ratio = (curr_high - max(curr_open, curr_close)) / rng

    above_ema20 = curr_close >= c_ema20
    above_neckline = True if (neckline is None or neckline <= 0) else (curr_close >= neckline)
    not_overbought = c_rsi <= 75.0
    no_strong_rejection = upper_wick_ratio <= 0.55 and (curr_close >= curr_open * 0.995)

    confirmed = bool(above_ema20 and above_neckline and not_overbought and no_strong_rejection)
    if confirmed:
        reason = f"Confirmación 1H OK: cierre ${curr_close:.2f} >= EMA20_1H ${c_ema20:.2f}, RSI_1H {c_rsi:.1f}."
    else:
        fails = []
        if not above_neckline and neckline:
            fails.append(f"bajo neckline ${neckline:.2f}")
        if not above_ema20:
            fails.append(f"bajo EMA20_1H ${c_ema20:.2f}")
        if not not_overbought:
            fails.append(f"RSI_1H sobrecomprado ({c_rsi:.1f})")
        if not no_strong_rejection:
            fails.append("rechazo bajista en vela 1H")
        reason = "Esperando confirmación 1H: " + ", ".join(fails) + "."

    return {
        "available": True,
        "confirmed": confirmed,
        "hourly_close": round(curr_close, 2),
        "hourly_ema20": round(c_ema20, 2),
        "hourly_rsi": round(c_rsi, 1),
        "reason": reason,
    }


def analyze_chart_patterns(
    df_daily: pd.DataFrame,
    df_1h: Optional[pd.DataFrame] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Ejecuta todos los detectores de figuras alcistas sobre 1D y devuelve el mejor patrón
    junto con la confirmación horaria (1H) si está disponible.
    """
    candidates: list[dict[str, Any]] = []
    for detector in (
        detect_inverse_head_and_shoulders,
        detect_double_or_triple_bottom,
        detect_consolidation_breakout,
    ):
        res = detector(df_daily, params=params)
        if res is not None:
            candidates.append(res)

    if not candidates:
        hourly = confirm_hourly_entry(df_1h, neckline=None) if df_1h is not None else {"available": False, "confirmed": False, "reason": "Sin datos 1H."}
        return {
            "has_pattern": False,
            "best_pattern": None,
            "all_patterns": [],
            "pattern_status": "NONE",
            "hourly_confirmation": hourly,
        }

    # Priorizar CONFIRMED sobre FORMING y luego por confianza
    candidates.sort(
        key=lambda x: (1 if x["status"] == "CONFIRMED" else 0, x["confidence"]),
        reverse=True,
    )
    best = candidates[0]
    hourly = confirm_hourly_entry(df_1h, neckline=best.get("neckline")) if df_1h is not None else {
        "available": False,
        "confirmed": False,
        "reason": "Sin datos 1H proporcionados.",
    }

    return {
        "has_pattern": True,
        "best_pattern": best,
        "all_patterns": candidates,
        "pattern_status": best["status"],
        "hourly_confirmation": hourly,
    }
