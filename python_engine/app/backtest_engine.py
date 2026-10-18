"""
Motor de Backtest Cuantitativo y Econométrico Riguroso en Modo Full Auto (Versión 6).
100% Agnóstico a la Compañía o Sector (Cero reglas arbitrarias por ticker o clúster).
Incorpora:
1. Eficiencia Fractal de Kaufman (ER_10 >= 0.14) para filtrar ruido browniano.
2. Volatilidad Relativa Endógena (ATR% >= 1.80%) y Escalado Continuo del Multiplicador ATR.
3. Banda de No-Agotamiento ADX (19.0 <= ADX <= 35.0) para evitar compras en clímax parabólicos.
4. Filtro de Deriva Mensual (Ret_20d >= -2.0%) y Liquidez Relativa (RVOL_20 >= 0.60).
5. Ranking por Esperanza Matemática Compuesta (ER_10 + Sharpe_20 + RVOL_20 + Banda ADX).
6. Bloqueo Progresivo de Beneficios en 2 Etapas (Breakeven en +0.85R -> +0.08R; Profit Ratchet en +1.45R -> +0.55R).
"""
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import ADXIndicator, EMAIndicator, SMAIndicator
from ta.volatility import AverageTrueRange

# Configuración del entorno de importación
sys.path.insert(0, str(Path(__file__).parent))
from audit_agent import DEFAULT_CLUSTERS, audit_agent, get_adaptive_allocation_pct

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backtester")


def evaluate_bar_signals(
    df: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    """
    Precalcula todos los indicadores técnicos, econométricos y estadísticos vectorizados
    sobre el DataFrame histórico de forma 100% agnóstica al activo.
    """
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_s = df["Open"] if "Open" in df.columns else close
    vol_s = df["Volume"] if "Volume" in df.columns else pd.Series(0.0, index=df.index)

    # 1. Indicadores de tendencia, estructura y volatilidad relativa endógena
    df["ema_20"] = EMAIndicator(close=close, window=20).ema_indicator()
    df["sma_50"] = SMAIndicator(close=close, window=50).sma_indicator()
    df["sma_200"] = SMAIndicator(close=close, window=200).sma_indicator()
    df["atr_14"] = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    df["atr_pct"] = (df["atr_14"] / close) * 100.0
    df["ema20_slope_5d"] = df["ema_20"].pct_change(5) * 100.0
    df["sma50_slope_5d"] = df["sma_50"].pct_change(5) * 100.0

    # 2. DMI / ADX
    adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
    df["adx"] = adx_ind.adx()
    df["adx_slope_3d"] = df["adx"].diff(3)
    df["plus_di"] = adx_ind.adx_pos()
    df["minus_di"] = adx_ind.adx_neg()
    df["di_spread"] = df["plus_di"] - df["minus_di"]

    # 3. Osciladores
    stoch = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=4)
    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()
    df["rsi_14"] = RSIIndicator(close=close, window=14).rsi()

    # 4. Volumen relativo, retornos multi-horizonte y Price Action
    vol_sma20 = vol_s.rolling(20).mean()
    df["rvol_20"] = np.where(vol_sma20 > 0, vol_s / vol_sma20, 1.0)
    df["ret_1d_pct"] = close.pct_change(1) * 100.0
    df["ret_3d_pct"] = close.pct_change(3) * 100.0
    df["ret_20d_pct"] = close.pct_change(20) * 100.0

    candle_range = (high - low).replace(0, 0.001)
    df["clv"] = (close - low) / candle_range
    df["candle_body"] = close - open_s
    df["candle_body_atr"] = np.where(df["atr_14"] > 0, df["candle_body"] / df["atr_14"], 0.0)

    # 5. Variables Predictivas Econométricas (Kaufman Efficiency Ratio & Rolling Sharpe 20d)
    net_chg_10 = close.diff(10).abs()
    sum_chg_10 = close.diff(1).abs().rolling(10).sum()
    df["er_10"] = (net_chg_10 / sum_chg_10.replace(0, np.nan)).fillna(0.0)

    ret1 = close.pct_change(1)
    df["sharpe_20"] = ((ret1.rolling(20).mean() / ret1.rolling(20).std().replace(0, np.nan)) * np.sqrt(252)).fillna(0.0)

    # 6. Escudo Anti-Cuchillo Cayendo
    severe_bearish = (df["candle_body_atr"] <= -0.80) | ((df["ret_1d_pct"] <= -2.8) & (df["clv"] < 0.25))
    waterfall = (df["ret_3d_pct"] <= -4.2) & (close < df["ema_20"]) & (df["minus_di"] > df["plus_di"]) & (df["candle_body"] < 0)
    distribution = (df["candle_body_atr"] < -0.55) & (df["rvol_20"] >= 1.25) & (df["clv"] < 0.35)
    df["falling_knife"] = severe_bearish | waterfall | distribution

    # 7. Parámetros Estadísticos Agnósticos
    adx_min = float(params.get("adx_bullish_min", 19.0))
    adx_max = float(params.get("adx_exhaustion_max", 35.0))
    stoch_os = float(params.get("stoch_bullish_oversold", 36.0))
    min_atr_pct = float(params.get("min_atr_volatility_pct", 1.80))
    min_er10 = float(params.get("min_kaufman_efficiency_ratio", 0.14))
    min_rvol = float(params.get("min_relative_volume_rvol", 0.60))
    min_ret20 = float(params.get("min_momentum_20d_pct", -2.0))

    above_200 = close > df["sma_200"]
    above_50 = close >= df["sma_50"] * 0.995
    above_20 = close >= df["ema_20"] * 0.997
    buyers_ctrl = df["plus_di"] >= df["minus_di"]

    prev_k = df["stoch_k"].shift(1)
    prev_d = df["stoch_d"].shift(1)
    stoch_cross_up = (prev_k <= prev_d) & (df["stoch_k"] > df["stoch_d"])
    stoch_rising = (df["stoch_k"] > df["stoch_d"]) & (df["stoch_k"] > prev_k) & (df["stoch_k"] <= 44.0)

    bullish_regime = above_200 & (above_20 | above_50) & buyers_ctrl & (~df["falling_knife"])
    tactical_trigger = (stoch_cross_up & (df["stoch_k"] <= stoch_os)) | (
        stoch_rising & (df["rsi_14"] >= 30.0) & (df["rsi_14"] <= 58.0) & (df["clv"] >= 0.45)
    )

    econometric_filter = (
        (df["adx"] >= adx_min)
        & (df["adx"] <= adx_max)
        & (df["atr_pct"] >= min_atr_pct)
        & (df["er_10"] >= min_er10)
        & (df["rvol_20"] >= min_rvol)
        & (df["ret_20d_pct"] >= min_ret20)
    )

    df["signal_buy"] = bullish_regime & tactical_trigger & econometric_filter

    # Esperanza Matemática Compuesta para priorizar candidatos simultáneos sin sesgo por empresa
    df["pred_edge"] = (
        (df["er_10"] * 35.0)
        + (df["sharpe_20"].clip(-1.0, 3.0) * 8.0)
        + ((32.0 - df["adx"]).clip(0.0, 12.0) * 1.5)
        + (df["rvol_20"].clip(0.5, 2.0) * 10.0)
    )
    return df


def run_full_auto_backtest(
    initial_capital: float = 5000.0,
    max_risk_pct: float = 0.01,
    atr_sl_mult: float = 1.5,
    tp_rr: float = 2.0,
    commission_per_order: float = 0.35,
    max_concurrent_positions: int = 4,
) -> dict[str, Any]:
    """Ejecuta la simulación cronológica día por día (3 años) con reglas 100% agnósticas al activo."""
    params = audit_agent.strategy_params
    clusters = params.get("clusters") or DEFAULT_CLUSTERS
    all_tickers = []
    ticker_to_cluster = {}
    for c_id, t_list in clusters.items():
        for t in t_list:
            if t not in all_tickers:
                all_tickers.append(t)
                ticker_to_cluster[t] = c_id

    logger.info(f"Descargando datos históricos de 3 años para {len(all_tickers)} tickers + benchmarks SPY/QQQ...")
    clean_all = [t if t != "SQ" else "XYZ" for t in all_tickers]
    download_list = list(dict.fromkeys(clean_all + ["SPY", "QQQ"]))
    raw = yf.download(download_list, period="3y", interval="1d", group_by="ticker", threads=True, progress=False)

    # Preprocesar series macro de referencia (SPY y QQQ)
    benchmark_dfs: dict[str, pd.DataFrame] = {}
    for bm in ("SPY", "QQQ"):
        try:
            if bm in raw.columns.get_level_values(0):
                dfb = raw[bm].dropna(subset=["Open", "High", "Low", "Close"]).copy()
                dfb["ret_1d_pct"] = dfb["Close"].pct_change(1) * 100.0
                dfb["intraday_chg_pct"] = ((dfb["Close"] - dfb["Open"]) / dfb["Open"].replace(0, np.nan)) * 100.0
                dfb["effective_drop_pct"] = np.minimum(dfb["ret_1d_pct"].fillna(0.0), dfb["intraday_chg_pct"].fillna(0.0))
                dfb["typ_vwap"] = (dfb["High"] + dfb["Low"] + dfb["Close"]) / 3.0
                dfb["above_vwap"] = dfb["Close"] >= (dfb["typ_vwap"] * 0.999)
                benchmark_dfs[bm] = dfb
        except Exception as exc:
            logger.debug(f"Error procesando benchmark {bm}: {exc}")

    ticker_dfs = {}
    common_dates = None
    for t in all_tickers:
        try:
            tk = "XYZ" if t == "SQ" else t
            if tk not in raw.columns.get_level_values(0):
                continue
            df_t = raw[tk].dropna(subset=["Open", "High", "Low", "Close"])
            if len(df_t) < 60:
                continue
            df_t = evaluate_bar_signals(df_t, params)
            ticker_dfs[t] = df_t
            if common_dates is None:
                common_dates = df_t.index
            else:
                common_dates = common_dates.union(df_t.index)
        except Exception as e:
            logger.debug(f"Error procesando {t}: {e}")
            continue

    common_dates = sorted(common_dates)
    logger.info(f"Histórico sincronizado: {len(common_dates)} sesiones diarias disponibles.")

    portfolio_value = initial_capital
    cash = initial_capital
    open_positions: dict[str, dict[str, Any]] = {}
    closed_trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    circuit_breaker_triggered = False

    be_trigger_r = float(params.get("breakeven_trigger_r", 0.85))
    be_lock_r = float(params.get("breakeven_lock_r", 0.08))
    t1_trigger_r = float(params.get("trailing_ratchet_trigger_r", 1.45))
    t1_lock_r = float(params.get("trailing_ratchet_lock_r", 0.55))
    endogenous_scaling = bool(params.get("endogenous_atr_scaling_enabled", True))
    macro_gatekeeper_enabled = bool(params.get("macro_regime_gatekeeper_enabled", True))
    spy_thresh = float(params.get("macro_index_threshold_drop_pct", -0.75))
    qqq_thresh = float(params.get("macro_qqq_threshold_drop_pct", -1.00))
    vwap_filter = bool(params.get("macro_intraday_vwap_filter", True))
    min_breadth_ratio = float(params.get("macro_market_breadth_min_advancing_pct", 0.38))

    macro_vetoed_days = 0
    macro_vetoed_signals = 0

    warmup_days = 60
    sim_dates = common_dates[warmup_days:]

    for current_date in sim_dates:
        # 1. Evaluar posiciones abiertas contra los precios High/Low/Close de hoy
        closed_today = []
        for ticker, pos in list(open_positions.items()):
            df_t = ticker_dfs.get(ticker)
            if df_t is None or current_date not in df_t.index:
                continue
            row = df_t.loc[current_date]
            high_p = float(row["High"])
            low_p = float(row["Low"])
            close_p = float(row["Close"])
            sl = pos["stop_loss"]
            tp = pos["take_profit"]
            shares = pos["shares"]
            risk_dist = pos["risk_per_share"]
            entry_p = pos["entry_price"]

            exit_price = close_p
            exit_reason = ""

            # Orden conservador intrabarra: evaluar stop vigente antes de subir el ratchet con el High de hoy
            if low_p <= sl:
                exit_price = sl
                if pos["stage"] == 0:
                    exit_reason = "STOP_LOSS_HIT"
                elif pos["stage"] == 1:
                    exit_reason = "BREAKEVEN_STOP_HIT"
                else:
                    exit_reason = "TRAILING_PROFIT_HIT"
            elif high_p >= tp:
                exit_price = tp
                exit_reason = "TAKE_PROFIT_HIT"
            else:
                # Actualizar tramos de protección progresiva para sesiones siguientes
                if high_p >= entry_p + (t1_trigger_r * risk_dist):
                    pos["stage"] = max(pos["stage"], 2)
                    pos["stop_loss"] = max(pos["stop_loss"], round(entry_p + (t1_lock_r * risk_dist), 2))
                elif high_p >= entry_p + (be_trigger_r * risk_dist):
                    pos["stage"] = max(pos["stage"], 1)
                    pos["stop_loss"] = max(pos["stop_loss"], round(entry_p + (be_lock_r * risk_dist), 2))

                if pos["bars_held"] >= 15:
                    exit_price = close_p
                    exit_reason = "TIME_EXPIRY_15D"
                else:
                    pos["bars_held"] += 1
                    pos["current_price"] = close_p
                    continue

            # Liquidación de posición
            gross_pnl = (exit_price - entry_p) * shares
            commissions = commission_per_order * 2.0
            net_pnl = gross_pnl - commissions
            cash += (shares * exit_price) - commission_per_order
            portfolio_value += net_pnl

            closed_trade = {
                "ticker": ticker,
                "cluster_id": pos["cluster_id"],
                "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                "exit_date": current_date.strftime("%Y-%m-%d"),
                "bars_held": pos["bars_held"],
                "entry_price": round(entry_p, 2),
                "exit_price": round(exit_price, 2),
                "shares": shares,
                "stop_loss": sl,
                "take_profit": tp,
                "net_pnl_usd": round(net_pnl, 2),
                "net_pnl_pct": round((net_pnl / pos["capital_invested"]) * 100.0, 2),
                "exit_reason": exit_reason,
                "r_multiple": round(net_pnl / (risk_dist * shares), 2) if risk_dist > 0 else 0.0,
            }
            closed_trades.append(closed_trade)
            closed_today.append(ticker)

        for t in closed_today:
            del open_positions[t]

        # 2. Verificar cortafuegos de cartera (-4% desde el equity inicial)
        drawdown_pct = ((initial_capital - portfolio_value) / initial_capital)
        if drawdown_pct >= 0.04 and not circuit_breaker_triggered:
            circuit_breaker_triggered = True
            logger.warning(f"¡CORTAFUEGOS 4% ACTIVADO en {current_date.strftime('%Y-%m-%d')}! Drawdown: {drawdown_pct*100:.2f}%")

        # 2.5 Evaluar Filtro de Régimen Macro (Macro Regime Gatekeeper) para el día actual
        macro_veto_today = False
        if macro_gatekeeper_enabled:
            spy_daily = 0.0
            spy_intraday = 0.0
            qqq_daily = 0.0
            qqq_intraday = 0.0
            spy_above_vwap = True
            qqq_above_vwap = True

            if "SPY" in benchmark_dfs and current_date in benchmark_dfs["SPY"].index:
                s_row = benchmark_dfs["SPY"].loc[current_date]
                spy_daily = float(s_row.get("ret_1d_pct", 0.0))
                spy_intraday = float(s_row.get("intraday_chg_pct", 0.0))
                spy_above_vwap = bool(s_row.get("above_vwap", True))

            if "QQQ" in benchmark_dfs and current_date in benchmark_dfs["QQQ"].index:
                q_row = benchmark_dfs["QQQ"].loc[current_date]
                qqq_daily = float(q_row.get("ret_1d_pct", 0.0))
                qqq_intraday = float(q_row.get("intraday_chg_pct", 0.0))
                qqq_above_vwap = bool(q_row.get("above_vwap", True))

            # Amplitud diaria del universo
            adv_count = 0
            tot_count = 0
            for df_t in ticker_dfs.values():
                if current_date in df_t.index:
                    tot_count += 1
                    if float(df_t.loc[current_date].get("ret_1d_pct", 0.0)) > 0.0:
                        adv_count += 1
            adv_ratio = (adv_count / tot_count) if tot_count > 0 else 0.50

            spy_breach = (
                (spy_daily <= spy_thresh and spy_intraday <= -0.30 and not spy_above_vwap)
                or (spy_intraday <= (spy_thresh * 1.5) and spy_daily < 0.0 and not spy_above_vwap)
            )
            qqq_breach = (
                (qqq_daily <= qqq_thresh and qqq_intraday <= -0.40 and not qqq_above_vwap)
                or (qqq_intraday <= (qqq_thresh * 1.5) and qqq_daily < 0.0 and not qqq_above_vwap)
            )
            vwap_breadth_breach = (
                vwap_filter
                and (not spy_above_vwap)
                and (not qqq_above_vwap)
                and (spy_daily <= -0.50)
                and (adv_ratio < min_breadth_ratio)
            )
            if spy_breach or qqq_breach or vwap_breadth_breach:
                macro_veto_today = True
                macro_vetoed_days += 1

        # 3. Si hay cupo para nuevas posiciones y no saltó el cortafuegos, escanear candidatos
        if not circuit_breaker_triggered and len(open_positions) < max_concurrent_positions:
            if macro_veto_today:
                # Contabilizar cuántas señales técnicas fueron blindadas por el Macro Gatekeeper hoy
                for ticker, df_t in ticker_dfs.items():
                    if ticker not in open_positions and current_date in df_t.index:
                        if bool(df_t.loc[current_date].get("signal_buy", False)):
                            macro_vetoed_signals += 1
            else:
                candidates = []
                for ticker, df_t in ticker_dfs.items():
                    if ticker in open_positions or current_date not in df_t.index:
                        continue
                    row = df_t.loc[current_date]
                    if bool(row.get("signal_buy", False)):
                        close_p = float(row["Close"])
                        atr_v = float(row["atr_14"])
                        atr_p = float(row["atr_pct"])
                        if atr_v <= 0:
                            continue

                        # Multiplicador ATR 100% Endógeno (basado únicamente en el ATR% de la serie, sin reglas por empresa)
                        if endogenous_scaling:
                            ticker_atr_mult = min(2.0, max(1.5, atr_sl_mult + 0.12 * (atr_p - 1.8)))
                        else:
                            ticker_atr_mult = atr_sl_mult

                        sl_p = round(close_p - (ticker_atr_mult * atr_v), 2)
                        risk_dist = close_p - sl_p
                        if risk_dist <= 0:
                            continue
                        tp_p = round(close_p + (tp_rr * risk_dist), 2)

                        # Dimensionamiento
                        max_alloc_pct, _ = get_adaptive_allocation_pct(portfolio_value, params)
                        max_loss = portfolio_value * max_risk_pct
                        risk_budget = max_loss - (commission_per_order * 2.0)
                        if risk_budget <= 0:
                            continue
                        shares_by_risk = math.floor(risk_budget / risk_dist)
                        max_capital = portfolio_value * max_alloc_pct
                        shares_by_cap = math.floor((max_capital - commission_per_order) / close_p)
                        shares = min(shares_by_risk, shares_by_cap)

                        if shares >= 1:
                            capital_needed = (shares * close_p) + commission_per_order
                            if cash >= capital_needed:
                                candidates.append({
                                    "ticker": ticker,
                                    "cluster_id": ticker_to_cluster.get(ticker, "UNKNOWN"),
                                    "close": close_p,
                                    "sl": sl_p,
                                    "tp": tp_p,
                                    "risk_dist": risk_dist,
                                    "shares": shares,
                                    "capital_needed": capital_needed,
                                    "pred_edge": float(row.get("pred_edge", 25.0)),
                                })

                # Ordenar candidatos por Esperanza Matemática Compuesta (pred_edge)
                candidates.sort(key=lambda c: c["pred_edge"], reverse=True)

                # Abrir posiciones hasta el cupo máximo
                slots_available = max_concurrent_positions - len(open_positions)
                for cand in candidates[:slots_available]:
                    if cash >= cand["capital_needed"]:
                        cash -= cand["capital_needed"]
                        open_positions[cand["ticker"]] = {
                            "entry_date": current_date,
                            "entry_price": cand["close"],
                            "initial_stop_loss": cand["sl"],
                            "stop_loss": cand["sl"],
                            "take_profit": cand["tp"],
                            "shares": cand["shares"],
                            "risk_per_share": cand["risk_dist"],
                            "capital_invested": cand["capital_needed"],
                            "bars_held": 0,
                            "current_price": cand["close"],
                            "cluster_id": cand["cluster_id"],
                            "stage": 0,
                        }

        # Calcular valor total de la cartera al cierre del día
        total_equity = cash + sum(pos["shares"] * pos["current_price"] for pos in open_positions.values())
        equity_curve.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "equity": round(total_equity, 2),
            "cash": round(cash, 2),
            "open_positions_count": len(open_positions),
        })

    # Liquidar al cierre del backtest cualquier posición que quedara abierta
    last_date = sim_dates[-1]
    for ticker, pos in list(open_positions.items()):
        exit_price = pos["current_price"]
        shares = pos["shares"]
        gross_pnl = (exit_price - pos["entry_price"]) * shares
        commissions = commission_per_order * 2.0
        net_pnl = gross_pnl - commissions
        cash += (shares * exit_price) - commission_per_order
        portfolio_value += net_pnl
        closed_trades.append({
            "ticker": ticker,
            "cluster_id": pos["cluster_id"],
            "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": last_date.strftime("%Y-%m-%d"),
            "bars_held": pos["bars_held"],
            "entry_price": round(pos["entry_price"], 2),
            "exit_price": round(exit_price, 2),
            "shares": shares,
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
            "net_pnl_usd": round(net_pnl, 2),
            "net_pnl_pct": round((net_pnl / pos["capital_invested"]) * 100.0, 2),
            "exit_reason": "BACKTEST_END_CLOSE",
            "r_multiple": round(net_pnl / (pos["risk_per_share"] * shares), 2) if pos["risk_per_share"] > 0 else 0.0,
        })

    # Estadísticas Globales
    pnls = [t["net_pnl_usd"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_trades = len(closed_trades)
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    net_profit = sum(pnls)
    final_equity = initial_capital + net_profit
    total_return_pct = (net_profit / initial_capital) * 100.0

    # Drawdown máximo sobre la curva de equidad
    peak = initial_capital
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for pt in equity_curve:
        eq = pt["equity"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_p = (dd / peak) * 100.0 if peak > 0 else 0.0
        if dd > max_dd_usd:
            max_dd_usd = dd
        if dd_p > max_dd_pct:
            max_dd_pct = dd_p

    # Desglose por Clúster (meramente informativo/observacional; el motor no usa el clúster para decidir)
    by_cluster = {}
    for t in closed_trades:
        cid = t["cluster_id"]
        c_stats = by_cluster.setdefault(cid, {"trades": 0, "wins": 0, "pnl": 0.0})
        c_stats["trades"] += 1
        if t["net_pnl_usd"] > 0:
            c_stats["wins"] += 1
        c_stats["pnl"] = round(c_stats["pnl"] + t["net_pnl_usd"], 2)

    for cid, s in by_cluster.items():
        s["win_rate_pct"] = round((s["wins"] / s["trades"]) * 100.0, 1) if s["trades"] > 0 else 0.0

    exit_counts = {}
    for t in closed_trades:
        r = t["exit_reason"]
        exit_counts[r] = exit_counts.get(r, 0) + 1

    report = {
        "summary": {
            "initial_capital_usd": initial_capital,
            "final_equity_usd": round(final_equity, 2),
            "net_profit_usd": round(net_profit, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_closed_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "circuit_breaker_triggered": circuit_breaker_triggered,
            "avg_trade_pnl_usd": round(net_profit / total_trades, 2) if total_trades > 0 else 0.0,
            "avg_r_multiple": round(sum(t["r_multiple"] for t in closed_trades) / total_trades, 2) if total_trades > 0 else 0.0,
            "macro_gatekeeper_enabled": macro_gatekeeper_enabled,
            "macro_vetoed_days": macro_vetoed_days,
            "macro_vetoed_signals": macro_vetoed_signals,
        },
        "exit_reasons": exit_counts,
        "cluster_breakdown": by_cluster,
        "sample_trades": closed_trades[:15],
        "all_trades": closed_trades,
        "equity_curve": equity_curve[::5],
    }

    out_file = Path(__file__).parent / "audit_data" / "backtest_results.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Informe guardado en {out_file}")

    return report


if __name__ == "__main__":
    rep = run_full_auto_backtest()
    s = rep["summary"]
    print("\n" + "=" * 65)
    print("RESULTADOS BACKTEST ECONOMÉTRICO V7 (CON MACRO REGIME GATEKEEPER)")
    print("=" * 65)
    print(f"Capital Inicial:     ${s['initial_capital_usd']:,.2f}")
    print(f"Capital Final:       ${s['final_equity_usd']:,.2f} ({s['total_return_pct']:+.2f}%)")
    print(f"Beneficio Neto:      ${s['net_profit_usd']:+,.2f}")
    print(f"Total Operaciones:   {s['total_closed_trades']}")
    print(f"Ratio de Acierto:    {s['win_rate_pct']:.1f}% ({s['winning_trades']}W / {s['losing_trades']}L)")
    print(f"Profit Factor:       {s['profit_factor']:.2f}")
    print(f"Esperanza Media:     {s['avg_r_multiple']:+.2f} R (${s['avg_trade_pnl_usd']:+.2f}/trade)")
    print(f"Max Drawdown:        {s['max_drawdown_pct']:.2f}% (-${s['max_drawdown_usd']:,.2f})")
    print(f"Cortafuegos -4%:     {'ACTIVADO' if s['circuit_breaker_triggered'] else 'NO ACTIVADO (Protegido)'}")
    print(f"Macro Gatekeeper:    {s['macro_vetoed_days']} sesiones en Veto | {s['macro_vetoed_signals']} señales tóxicas bloqueadas")
    print("=" * 65)
    print("\nDesglose por Motivo de Salida:")
    for er, cnt in rep["exit_reasons"].items():
        print(f"• {er:22s}: {cnt:2d} trades ({cnt/s['total_closed_trades']*100:4.1f}%)")
    print("\nDesglose Observacional por Clúster (Sin reglas por clúster):")
    for cid, st in rep["cluster_breakdown"].items():
        print(f"• {cid:22s}: {st['trades']:2d} trades | Win Rate: {st['win_rate_pct']:5.1f}% | PnL: ${st['pnl']:+8.2f}")
