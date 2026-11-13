"""
Motor Cuantitativo Limpio: "Los 3 Trucos" + Comité de Admisión Pre-Entrada.

Diseño en 2 Capas Multi-Temporales (Solo Largos):
- Capa 1 (1D, caché 5 min): Tendencia sana (Precio > EMA20 > SMA50 > SMA200, +DI > -DI, sin desplome).
- Capa 2 (15m + 5m en vivo): VWAP de sesión, EMA20_15m, ATR_15m, Pullback a valor y Gatillo con volumen.
- Comité de Admisión (3 Especialistas con veto):
  1. Momento y Espacio Libre (sin sobrecompra ni techo pegado).
  2. Volumen y Divergencia (volumen comprador > volumen del retroceso).
  3. Empresa y Catalizador (vía ai_advisor estricto en pre-ejecución).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("quant-engine.three-tricks")

# Caché en memoria para barras diarias (1D): TTL 300 segundos
_DAILY_CACHE_LOCK = threading.Lock()
_DAILY_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_DAILY_CACHE_TTL_SECONDS = 300.0


# =====================================================================
# INDICADORES PUROS (PANDAS / NUMPY, DETERMINISTAS Y SIN DEPENDENCIAS)
# =====================================================================

def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()


def compute_rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14, smooth: int = 4
) -> tuple[pd.Series, pd.Series]:
    low_min = low.rolling(window=window, min_periods=window).min()
    high_max = high.rolling(window=window, min_periods=window).max()
    denom = (high_max - low_min).replace(0.0, np.nan)
    k = (((close - low_min) / denom) * 100.0).fillna(50.0)
    d = k.rolling(window=smooth, min_periods=1).mean().fillna(50.0)
    return k, d


def compute_dmi(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )
    atr = compute_atr(high, low, close, window=window).replace(0.0, np.nan)
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean() / atr)
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean() / atr)
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)).fillna(0.0)
    adx = dx.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean().fillna(20.0)
    return adx, plus_di.fillna(20.0), minus_di.fillna(20.0)


def compute_session_vwap(df_intraday: pd.DataFrame) -> float:
    """Calcula el VWAP institucional de la última sesión disponible en el DataFrame intradía."""
    if df_intraday is None or df_intraday.empty:
        return 0.0
    last_date = df_intraday.index[-1].date() if hasattr(df_intraday.index[-1], "date") else None
    session_df = df_intraday[df_intraday.index.date == last_date] if last_date is not None else df_intraday
    if session_df.empty:
        session_df = df_intraday.tail(26)
    typ = (session_df["High"] + session_df["Low"] + session_df["Close"]) / 3.0
    vol = session_df["Volume"].fillna(0.0)
    vol_sum = float(vol.sum())
    if vol_sum > 0:
        return float((typ * vol).sum() / vol_sum)
    return float(session_df["Close"].iloc[-1])


# =====================================================================
# DESCARGA MULTI-TEMPORAL EN 2 CAPAS (ALPACA DATA + FALLBACK YFINANCE)
# =====================================================================

def _normalize_ohlcv(df: pd.DataFrame, ticker: Optional[str] = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        if ticker and ticker in out.columns.get_level_values(0):
            out = out[ticker]
        elif ticker and ticker in out.columns.get_level_values(-1):
            out.columns = out.columns.get_level_values(0)
        else:
            out.columns = out.columns.get_level_values(0)
    rename_map = {c: c.capitalize() for c in out.columns if isinstance(c, str) and c.lower() in ("open", "high", "low", "close", "volume")}
    if rename_map:
        out = out.rename(columns=rename_map)
    required = ["Open", "High", "Low", "Close"]
    if any(col not in out.columns for col in required):
        return pd.DataFrame()
    if "Volume" not in out.columns:
        out["Volume"] = 0.0
    return out.dropna(subset=required)


def get_cached_daily_batch(tickers: list[str], ttl_seconds: float = _DAILY_CACHE_TTL_SECONDS) -> dict[str, pd.DataFrame]:
    """
    Capa 1: Devuelve barras diarias (1Y, 1D) cacheadas durante 5 minutos por lote.
    Evita descargar 248 históricos diarios cada 10 segundos.
    """
    clean = [t.strip().upper() for t in tickers if t.strip()]
    now = time.monotonic()
    result: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    with _DAILY_CACHE_LOCK:
        for tk in clean:
            hit = _DAILY_CACHE.get(tk)
            if hit and (now - hit[0]) < ttl_seconds and not hit[1].empty:
                result[tk] = hit[1]
            else:
                missing.append(tk)

    if not missing:
        return result

    downloaded: dict[str, pd.DataFrame] = {}
    try:
        raw = yf.download(
            missing,
            period="1y",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
        )
        for tk in missing:
            try:
                df_tk = _normalize_ohlcv(raw if len(missing) == 1 else raw[tk], tk)
                if not df_tk.empty:
                    downloaded[tk] = df_tk
            except Exception:
                continue
    except Exception as exc:
        logger.warning(f"[Layer1-1D] Error en descarga batch ({len(missing)} tickers): {exc}")
        for tk in missing:
            try:
                raw_single = yf.download(tk, period="1y", interval="1d", progress=False)
                df_tk = _normalize_ohlcv(raw_single, tk)
                if not df_tk.empty:
                    downloaded[tk] = df_tk
            except Exception:
                continue

    now_store = time.monotonic()
    with _DAILY_CACHE_LOCK:
        for tk, df_tk in downloaded.items():
            _DAILY_CACHE[tk] = (now_store, df_tk)
            result[tk] = df_tk

    return result


def _fetch_alpaca_intraday_batch(
    tickers: list[str], timeframe_str: str, lookback_days: int
) -> dict[str, pd.DataFrame]:
    """Consulta barras intradía en batch desde Alpaca Data API si hay credenciales."""
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key or "TU_API_KEY" in api_key.upper() or not tickers:
        return {}

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf = (
            TimeFrame(15, TimeFrameUnit.Minute)
            if timeframe_str == "15m"
            else TimeFrame(5, TimeFrameUnit.Minute)
        )
        client = StockHistoricalDataClient(api_key, secret_key)
        start_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        req = StockBarsRequest(symbol_or_symbols=tickers, timeframe=tf, start=start_dt)
        bars = client.get_stock_bars(req)
        df_all = bars.df
        if df_all is None or df_all.empty:
            return {}

        out: dict[str, pd.DataFrame] = {}
        if isinstance(df_all.index, pd.MultiIndex):
            for tk in tickers:
                if tk in df_all.index.get_level_values(0):
                    sub = df_all.loc[tk].copy()
                    sub = _normalize_ohlcv(sub)
                    if not sub.empty:
                        out[tk] = sub
        return out
    except Exception as exc:
        logger.debug(f"[AlpacaData-{timeframe_str}] Fallback a yfinance: {exc}")
        return {}


def fetch_intraday_bars_batch(
    tickers: list[str], interval: str = "15m", period: str = "5d"
) -> dict[str, pd.DataFrame]:
    """
    Capa 2: Descarga barras intradía (15m o 5m) solo para los candidatos pre-filtrados.
    Prioriza Alpaca Data API (cero retraso / sin rate-limit) y hace fallback a yfinance.
    """
    clean = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
    if not clean:
        return {}

    lookback = 5 if interval == "15m" else 2
    result = _fetch_alpaca_intraday_batch(clean, interval, lookback)
    missing = [tk for tk in clean if tk not in result]

    if missing:
        try:
            raw = yf.download(
                missing,
                period=period,
                interval=interval,
                group_by="ticker",
                threads=True,
                progress=False,
            )
            for tk in missing:
                try:
                    df_tk = _normalize_ohlcv(raw if len(missing) == 1 else raw[tk], tk)
                    if not df_tk.empty:
                        result[tk] = df_tk
                except Exception:
                    continue
        except Exception as exc:
            logger.debug(f"[Layer2-{interval}] Error yfinance fallback: {exc}")

    return result


# =====================================================================
# CAPA 1: EVALUACIÓN DE ESTRUCTURA DIARIA (FILTRO MACRO DEL ACTIVO)
# =====================================================================

def evaluate_daily_structure(df_daily: pd.DataFrame) -> Optional[dict[str, Any]]:
    """
    Evalúa la salud diaria (1D) del activo.
    Devuelve métricas base y `daily_bullish_ok` para decidir si pasa al microscopio intradía.
    """
    df = _normalize_ohlcv(df_daily)
    if df.empty or len(df) < 55:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_s = df["Open"]
    vol_s = df["Volume"]

    ema20 = compute_ema(close, 20)
    sma50 = compute_sma(close, 50)
    sma200_raw = compute_sma(close, 200)
    atr14 = compute_atr(high, low, close, 14)
    rsi14 = compute_rsi(close, 14)
    stoch_k, stoch_d = compute_stochastic(high, low, close, 14, 4)
    adx, plus_di, minus_di = compute_dmi(high, low, close, 14)

    c = float(close.iloc[-1])
    o = float(open_s.iloc[-1])
    h = float(high.iloc[-1])
    l = float(low.iloc[-1])
    prev_c = float(close.iloc[-2]) if len(close) >= 2 else c
    close_5d = float(close.iloc[-6]) if len(close) >= 6 else prev_c

    c_ema20 = float(ema20.iloc[-1]) if not pd.isna(ema20.iloc[-1]) else c
    prev_ema20_5d = float(ema20.iloc[-6]) if len(ema20) >= 6 and not pd.isna(ema20.iloc[-6]) else c_ema20
    ema20_slope_pct = ((c_ema20 - prev_ema20_5d) / prev_ema20_5d * 100.0) if prev_ema20_5d > 0 else 0.0

    c_sma50 = float(sma50.iloc[-1]) if not pd.isna(sma50.iloc[-1]) else c
    c_sma200 = float(sma200_raw.iloc[-1]) if not pd.isna(sma200_raw.iloc[-1]) else c_sma50
    c_atr = float(atr14.iloc[-1]) if not pd.isna(atr14.iloc[-1]) else (c * 0.02)
    c_rsi = float(rsi14.iloc[-1])
    c_k = float(stoch_k.iloc[-1])
    c_d = float(stoch_d.iloc[-1])
    c_adx = float(adx.iloc[-1])
    c_pdi = float(plus_di.iloc[-1])
    c_mdi = float(minus_di.iloc[-1])

    avg_vol20 = float(vol_s.rolling(20).mean().iloc[-1]) if len(vol_s) >= 20 else float(vol_s.mean())
    rvol_20 = (float(vol_s.iloc[-1]) / avg_vol20) if avg_vol20 > 0 else 1.0

    candle_range = max(0.0001, h - l)
    clv_1d = (c - l) / candle_range
    body_atr = (c - o) / c_atr if c_atr > 0 else 0.0
    ret_1d_pct = ((c - prev_c) / prev_c * 100.0) if prev_c > 0 else 0.0
    ret_5d_pct = ((c - close_5d) / close_5d * 100.0) if close_5d > 0 else 0.0

    falling_knife = bool(
        body_atr <= -0.80
        or (ret_1d_pct <= -2.8 and clv_1d < 0.35)
        or (c < c_ema20 and c_mdi > c_pdi and body_atr < -0.50)
    )

    daily_swing_high_20 = float(high.iloc[-20:-1].max()) if len(high) >= 20 else float(high.max())
    daily_trend_ok = bool(
        c > c_ema20 * 0.995
        and c_ema20 >= c_sma50 * 0.995
        and c_sma50 >= c_sma200 * 0.995
        and c > c_sma200
        and c_pdi >= c_mdi
        and not falling_knife
    )

    if falling_knife or (c < c_ema20 and c_mdi > c_pdi):
        regime = "BEARISH_BREAKDOWN" if c > c_sma200 else "BEARISH_TREND"
    elif daily_trend_ok:
        regime = "BULLISH_TREND" if c_adx >= 18.0 else "BULLISH_PULLBACK"
    else:
        regime = "LATERAL_RANGE" if c_adx < 18.0 else "NEUTRAL"

    return {
        "price": round(c, 2),
        "ema_20": round(c_ema20, 2),
        "ema20_slope_pct": round(ema20_slope_pct, 2),
        "sma_50": round(c_sma50, 2),
        "sma_200": round(c_sma200, 2),
        "atr_14": round(c_atr, 2),
        "rsi_14": round(c_rsi, 1),
        "stoch_k": round(c_k, 2),
        "stoch_d": round(c_d, 2),
        "adx": round(c_adx, 2),
        "plus_di": round(c_pdi, 1),
        "minus_di": round(c_mdi, 1),
        "di_spread": round(c_pdi - c_mdi, 1),
        "rvol_20": round(rvol_20, 2),
        "ret_1d_pct": round(ret_1d_pct, 2),
        "ret_5d_pct": round(ret_5d_pct, 2),
        "close_location_value": round(clv_1d, 2),
        "candle_body_atr": round(body_atr, 2),
        "falling_knife": falling_knife,
        "daily_swing_high_20": round(daily_swing_high_20, 2),
        "daily_trend_ok": daily_trend_ok,
        "regime": regime,
    }


# =====================================================================
# CAPA 2: LOS 3 TRUCOS INTRADÍA (15m + 5m) + COMITÉ DE ADMISIÓN
# =====================================================================

def extract_intraday_features(
    df_15m: pd.DataFrame,
    df_5m: Optional[pd.DataFrame] = None,
) -> Optional[dict[str, Any]]:
    """Extrae VWAP, EMA20_15m, ATR_15m, RSI_15m, señales de pullback, ignición y volumen."""
    d15 = _normalize_ohlcv(df_15m)
    if d15.empty or len(d15) < 22:
        return None

    close_15 = d15["Close"]
    high_15 = d15["High"]
    low_15 = d15["Low"]
    open_15 = d15["Open"]
    vol_15 = d15["Volume"]

    ema20_15 = compute_ema(close_15, 20)
    atr_15 = compute_atr(high_15, low_15, close_15, 14)
    rsi_15 = compute_rsi(close_15, 14)
    vwap = compute_session_vwap(d15)

    # Usamos el timeframe más fino disponible (5m si tiene suficientes velas, si no 15m) para el gatillo exacto
    d_trig = _normalize_ohlcv(df_5m) if df_5m is not None else pd.DataFrame()
    if d_trig.empty or len(d_trig) < 12:
        d_trig = d15
        trigger_tf = "15m"
    else:
        trigger_tf = "5m"

    t_close = d_trig["Close"]
    t_high = d_trig["High"]
    t_low = d_trig["Low"]
    t_open = d_trig["Open"]
    t_vol = d_trig["Volume"]

    curr_px = float(t_close.iloc[-1])
    curr_open = float(t_open.iloc[-1])
    curr_high = float(t_high.iloc[-1])
    curr_low = float(t_low.iloc[-1])
    prev_high = float(t_high.iloc[-2])

    c_ema20_15 = float(ema20_15.iloc[-1])
    prev_ema20_15 = float(ema20_15.iloc[-4]) if len(ema20_15) >= 4 else c_ema20_15
    ema20_15_slope_pct = ((c_ema20_15 - prev_ema20_15) / prev_ema20_15 * 100.0) if prev_ema20_15 > 0 else 0.0
    c_atr_15 = float(atr_15.iloc[-1]) if not pd.isna(atr_15.iloc[-1]) and float(atr_15.iloc[-1]) > 0 else (curr_px * 0.006)
    c_rsi_15 = float(rsi_15.iloc[-1])

    # Anatomía de la vela gatillo
    rng = max(0.0001, curr_high - curr_low)
    clv = (curr_px - curr_low) / rng
    upper_wick_ratio = (curr_high - max(curr_open, curr_px)) / rng

    # RVOL de la vela gatillo respecto a las últimas 20 velas del timeframe gatillo
    avg_t_vol = float(t_vol.iloc[-21:-1].mean()) if len(t_vol) >= 6 else float(t_vol.mean())
    curr_t_vol = float(t_vol.iloc[-1])
    rvol_intraday = (curr_t_vol / avg_t_vol) if avg_t_vol > 0 else 1.0

    # Detección de sobreextensión vertical (3+ velas verdes grandes seguidas sin descanso)
    recent_3_green = (
        len(d_trig) >= 4
        and all(float(t_close.iloc[-i]) > float(t_open.iloc[-i]) for i in (1, 2, 3))
        and ((float(t_close.iloc[-1]) - float(t_open.iloc[-3])) > (1.8 * c_atr_15))
    )
    dist_from_ema20_atr = (curr_px - c_ema20_15) / max(0.0001, c_atr_15)

    # Pullback en las últimas 1-4 velas hacia la zona de valor (VWAP o EMA20_15m)
    recent_window = d_trig.iloc[-5:-1] if len(d_trig) >= 5 else d_trig.iloc[:-1]
    pullback_low = float(min(curr_low, recent_window["Low"].min())) if not recent_window.empty else curr_low
    value_zone_ceiling = max(vwap, c_ema20_15) + (0.50 * c_atr_15)
    value_zone_floor = min(vwap, c_ema20_15) - (0.60 * c_atr_15)
    touched_value_zone = bool(
        not recent_window.empty
        and (float(recent_window["Low"].min()) <= value_zone_ceiling)
        and (float(recent_window["Low"].min()) >= value_zone_floor)
    )

    # Volumen en velas rojas del descanso vs volumen de la vela de ignición/impulso
    pullback_reds = recent_window[recent_window["Close"] < recent_window["Open"]] if not recent_window.empty else pd.DataFrame()
    avg_red_pb_vol = float(pullback_reds["Volume"].mean()) if not pullback_reds.empty else 0.0
    green_impulse = d_trig.iloc[-12:-1]
    green_impulse = green_impulse[green_impulse["Close"] > green_impulse["Open"]]
    avg_green_imp_vol = float(green_impulse["Volume"].mean()) if not green_impulse.empty else curr_t_vol

    # Resistencia intradía inmediata (máximo previo excluyendo la vela actual)
    intra_swing_high = float(high_15.iloc[-25:-1].max()) if len(high_15) >= 25 else float(high_15.iloc[:-1].max())

    return {
        "trigger_timeframe": trigger_tf,
        "price": round(curr_px, 2),
        "vwap": round(vwap, 2),
        "ema20_15m": round(c_ema20_15, 2),
        "ema20_15m_slope_pct": round(ema20_15_slope_pct, 3),
        "atr_15m": round(c_atr_15, 4),
        "rsi_15m": round(c_rsi_15, 1),
        "prev_high": round(prev_high, 2),
        "pullback_low": round(pullback_low, 2),
        "intraday_swing_high": round(intra_swing_high, 2),
        "clv": round(clv, 2),
        "upper_wick_ratio": round(upper_wick_ratio, 2),
        "candle_green": bool(curr_px > curr_open),
        "breaks_prev_high": bool(curr_px > prev_high),
        "rvol_intraday": round(rvol_intraday, 2),
        "dist_from_ema20_atr": round(dist_from_ema20_atr, 2),
        "recent_3_green_vertical": bool(recent_3_green),
        "touched_value_zone": touched_value_zone,
        "curr_vol": curr_t_vol,
        "avg_red_pb_vol": avg_red_pb_vol,
        "avg_green_imp_vol": avg_green_imp_vol,
    }


def evaluate_three_tricks(
    daily: dict[str, Any],
    intraday: Optional[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Evalúa los 3 Trucos Simples y Deterministas + Comité Cuantitativo Pre-Entrada (Subagentes 1 y 2).
    """
    max_ext_atr = float(params.get("max_ema_extension_atr", 1.20))
    min_clv = float(params.get("min_intraday_clv", 0.65))
    max_upper_wick = float(params.get("max_upper_wick_ratio", 0.25))
    min_rvol_intra = float(params.get("min_intraday_rvol", 1.25))
    max_rsi_15m = float(params.get("max_intraday_rsi_entry", 70.0))

    # --- TRUCO 1: FILTRO DE MAREA (DIARIO + VWAP INTRADÍA) ---
    above_vwap = bool(intraday and intraday["price"] >= intraday["vwap"] * 0.999)
    ema15_rising = bool(intraday and intraday["ema20_15m_slope_pct"] >= -0.02)
    trick1_tide = bool(daily["daily_trend_ok"] and above_vwap and ema15_rising)

    # --- TRUCO 2: ANTI-SOBREPRECIO + PULLBACK A ZONA DE VALOR ---
    not_overextended = bool(
        intraday
        and intraday["dist_from_ema20_atr"] <= max_ext_atr
        and not intraday["recent_3_green_vertical"]
    )
    in_pullback_value = bool(intraday and intraday["touched_value_zone"])
    trick2_value = bool(not_overextended and in_pullback_value)

    # --- TRUCO 3: GATILLO DE IGNICIÓN CON VOLUMEN ---
    trick3_ignition = bool(
        intraday
        and intraday["candle_green"]
        and intraday["breaks_prev_high"]
        and intraday["clv"] >= min_clv
        and intraday["upper_wick_ratio"] <= max_upper_wick
        and intraday["rvol_intraday"] >= min_rvol_intra
    )

    # --- COMITÉ CUANTITATIVO PRE-ENTRADA (SUBAGENTES 1 Y 2) ---
    # Subagente 1: Momento del Trade (sin sobrecompra ni clímax)
    subagent_momento_ok = bool(intraday and intraday["rsi_15m"] <= max_rsi_15m and not_overextended)

    # Subagente 2: Volumen y Divergencia (volumen comprador supera al volumen vendedor del retroceso)
    if intraday:
        vol_vs_pullback = (
            intraday["curr_vol"] >= (intraday["avg_red_pb_vol"] * 1.05)
            if intraday["avg_red_pb_vol"] > 0
            else True
        )
        imp_vs_pullback = (
            intraday["avg_green_imp_vol"] >= (intraday["avg_red_pb_vol"] * 0.95)
            if intraday["avg_red_pb_vol"] > 0
            else True
        )
        subagent_volumen_ok = bool(vol_vs_pullback and imp_vs_pullback)
    else:
        subagent_volumen_ok = False

    # Puntuación transparente 0-100 (sin cajas negras)
    score = 0
    if daily["daily_trend_ok"]:
        score += 30
    if above_vwap and ema15_rising:
        score += 15
    if not_overextended:
        score += 10
    if in_pullback_value:
        score += 15
    if trick3_ignition:
        score += 20
    if subagent_momento_ok and subagent_volumen_ok:
        score += 10
    if daily["falling_knife"]:
        score = max(0, score - 40)

    signal_triggered = bool(
        trick1_tide
        and trick2_value
        and trick3_ignition
        and subagent_momento_ok
        and subagent_volumen_ok
    )

    block_reason: Optional[str] = None
    if daily["falling_knife"]:
        block_reason = f"Escudo Anti-Cuchillo Cayendo: caída diaria {daily['ret_1d_pct']:+.2f}%."
    elif not daily["daily_trend_ok"]:
        block_reason = "Truco 1 (Diario): sin estructura alcista limpia (EMA20 > SMA50 > SMA200 y +DI > -DI)."
    elif not intraday:
        block_reason = "Sin velas intradía (15m/5m) disponibles para validar el gatillo."
    elif not above_vwap:
        block_reason = f"Truco 1 (Intradía): precio ${intraday['price']:.2f} bajo VWAP ${intraday['vwap']:.2f}."
    elif not not_overextended:
        block_reason = (
            f"Truco 2 (Anti-Sobreprecio): extendido +{intraday['dist_from_ema20_atr']:.2f}x ATR_15m sobre EMA20_15m."
        )
    elif not in_pullback_value:
        block_reason = "Truco 2 (Pullback): esperando descanso a zona de valor (VWAP / EMA20_15m)."
    elif not trick3_ignition:
        block_reason = (
            f"Truco 3 (Gatillo): falta ignición alcista con volumen "
            f"(Verde={intraday['candle_green']}, RVOL={intraday['rvol_intraday']:.2f}x, CLV={intraday['clv']:.2f})."
        )
    elif not subagent_momento_ok:
        block_reason = f"Comité [Momento]: RSI_15m ({intraday['rsi_15m']:.1f}) en sobrecompra (> {max_rsi_15m:.0f})."
    elif not subagent_volumen_ok:
        block_reason = "Comité [Volumen]: divergencia detectada (volumen del retroceso supera al impulso comprador)."

    criteria = [
        {
            "label": "Truco 1 · Marea Alcista (1D + Sobre VWAP)",
            "passed": trick1_tide,
            "detail": (
                f"1D {'OK' if daily['daily_trend_ok'] else 'NO'} (+DI {daily['plus_di']:.1f}/-DI {daily['minus_di']:.1f}) · "
                + (
                    f"Precio ${intraday['price']:.2f} vs VWAP ${intraday['vwap']:.2f}"
                    if intraday
                    else "Sin intradía"
                )
            ),
        },
        {
            "label": "Truco 2 · Valor sin Sobreprecio (Pullback a VWAP/EMA20)",
            "passed": trick2_value,
            "detail": (
                f"Distancia EMA20_15m: {intraday['dist_from_ema20_atr']:+.2f}x ATR | Pullback: {'Sí' if in_pullback_value else 'No'}"
                if intraday
                else "Esperando datos intradía"
            ),
        },
        {
            "label": "Truco 3 · Gatillo de Ignición + Comité (Momento y Volumen)",
            "passed": bool(trick3_ignition and subagent_momento_ok and subagent_volumen_ok),
            "detail": (
                f"TF {intraday['trigger_timeframe']} · RVOL {intraday['rvol_intraday']:.2f}x · CLV {intraday['clv']*100:.0f}% · "
                f"Comité: Momento={'OK' if subagent_momento_ok else 'VETO'} / Vol={'OK' if subagent_volumen_ok else 'VETO'}"
                if intraday
                else "Sin gatillo intradía"
            ),
        },
    ]

    if signal_triggered:
        expert_summary = (
            f"3 Trucos + Comité APROBADO ({intraday['trigger_timeframe']}): sobre VWAP ${intraday['vwap']:.2f}, "
            f"pullback limpio y vela de ignición con RVOL {intraday['rvol_intraday']:.2f}x."
        )
    else:
        expert_summary = block_reason or "En vigilancia de estructura y pullback."

    return {
        "signal_triggered": signal_triggered,
        "setup_archetype": "THREE_TRICKS_INTRADAY_PULLBACK" if signal_triggered else "NONE",
        "quant_score": score,
        "short_term_bias": (
            "LONG_CONVICTION"
            if signal_triggered
            else ("SHORT_PRESSURE" if daily["falling_knife"] or "BEARISH" in daily["regime"] else "PULLBACK_WATCH")
        ),
        "block_reason": block_reason,
        "expert_summary": expert_summary,
        "expert_criteria": criteria,
        "committee_pre_trade": {
            "momento_subagent": "APPROVE" if subagent_momento_ok else "REJECT",
            "volumen_subagent": "APPROVE" if subagent_volumen_ok else "REJECT",
            "empresa_subagent": "PENDING_AI",
        },
    }
