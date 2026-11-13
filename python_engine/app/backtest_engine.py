"""
Motor de Backtest Cuantitativo "Los 3 Trucos" (V50) — Sencillo, Robusto y Agnóstico al Activo.
1. Truco 1 (Marea): Close > SMA200, EMA20 >= SMA50*0.995, +DI >= -DI y pendiente EMA20 >= 0.
2. Truco 2 (Pullback sin sobreprecio): Precio tocando zona de valor EMA20 sin 3 velas verdes verticales (>1.2xATR).
3. Truco 3 (Gatillo de ignición con volumen): Vela verde rompiendo High[1], CLV >= 0.65, mecha superior <= 0.25, RVOL >= 1.25.
4. Gestión viva: Breakeven+ a +0.60R, Trailing Ratchet 60%/75% MFE a +1.0R/+1.4R, riesgo 0.28% y comisión $1.00/orden.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from audit_agent import DEFAULT_CLUSTERS, audit_agent
from trigger_agent import trigger_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("backtester")


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    tr = pd.concat(
        [(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False).mean()


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / window, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)


def _dmi(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> tuple[pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr_s = _atr(high, low, close, window).replace(0.0, np.nan)
    pdi = 100.0 * pd.Series(plus_dm, index=high.index).ewm(alpha=1.0 / window, adjust=False).mean() / atr_s
    mdi = 100.0 * pd.Series(minus_dm, index=high.index).ewm(alpha=1.0 / window, adjust=False).mean() / atr_s
    return pdi.fillna(20.0), mdi.fillna(20.0)


def evaluate_bar_signals(
    df: pd.DataFrame,
    params: dict[str, Any],
    spy_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Evaluación vectorizada de Los 3 Trucos + Comité #1 (Momento) y #2 (Volumen)."""
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_s = df["Open"] if "Open" in df.columns else close
    vol_s = df["Volume"] if "Volume" in df.columns else pd.Series(0.0, index=df.index)

    df["ema_20"] = _ema(close, 20)
    df["sma_50"] = close.rolling(50).mean()
    df["sma_200"] = close.rolling(200).mean()
    df["atr_14"] = _atr(high, low, close, 14)
    df["atr_pct"] = (df["atr_14"] / close.replace(0.0, np.nan)) * 100.0
    df["ema20_slope_5d"] = ((df["ema_20"] - df["ema_20"].shift(5)) / df["ema_20"].shift(5).replace(0.0, np.nan)) * 100.0
    df["plus_di"], df["minus_di"] = _dmi(high, low, close, 14)
    df["di_spread"] = df["plus_di"] - df["minus_di"]
    df["rsi_14"] = _rsi(close, 14)

    vol_sma20 = vol_s.rolling(20).mean().replace(0.0, np.nan)
    df["rvol_20"] = (vol_s / vol_sma20).fillna(1.0)
    df["ret_1d_pct"] = close.pct_change(1) * 100.0
    df["ret_3d_pct"] = close.pct_change(3) * 100.0

    candle_range = (high - low).replace(0.0, 0.0001)
    df["clv"] = (close - low) / candle_range
    df["candle_body"] = close - open_s
    df["candle_body_atr"] = np.where(df["atr_14"] > 0, df["candle_body"] / df["atr_14"], 0.0)
    df["upper_wick_ratio"] = (high - np.maximum(open_s, close)) / candle_range
    df["recent_swing_high"] = high.shift(1).rolling(20, min_periods=5).max()
    df["pullback_low"] = low.rolling(4, min_periods=1).min()

    # Anti-Cuchillo Cayendo
    severe_bear = (df["candle_body_atr"] <= -0.80) | ((df["ret_1d_pct"] <= -2.8) & (df["clv"] < 0.25))
    waterfall = (df["ret_3d_pct"] <= -4.2) & (close < df["ema_20"]) & (df["minus_di"] > df["plus_di"]) & (df["candle_body"] < 0)
    df["falling_knife"] = severe_bear | waterfall

    # Truco 1: Marea Alcista
    trick1_tide = (
        (close > df["sma_200"])
        & (df["ema_20"] >= df["sma_50"] * 0.995)
        & (df["ema20_slope_5d"] >= -0.05)
        & (df["di_spread"] >= 0.0)
        & (~df["falling_knife"])
    )

    # Truco 2: Zona de valor (pullback a EMA20 sin sobreprecio ni 3 velas verticales seguidas)
    dist_ema_atr = (close - df["ema_20"]) / df["atr_14"].replace(0.0, np.nan)
    three_green = (close > open_s) & (close.shift(1) > open_s.shift(1)) & (close.shift(2) > open_s.shift(2))
    touched_ema = (low <= df["ema_20"] * 1.008) | (low.shift(1) <= df["ema_20"].shift(1) * 1.008)
    trick2_pullback = touched_ema & (dist_ema_atr <= float(params.get("max_dist_from_ema20_atr", 1.20))) & (~three_green)

    # Truco 3: Ignición con volumen
    min_rvol = float(params.get("min_rvol_intraday", 1.25))
    min_clv = float(params.get("min_clv_ignition", 0.65))
    max_wick = float(params.get("max_upper_wick_ratio", 0.25))
    trick3_ignition = (
        (close > open_s)
        & (close > high.shift(1))
        & (df["clv"] >= min_clv)
        & (df["upper_wick_ratio"] <= max_wick)
        & (df["rvol_20"] >= min_rvol)
    )

    # Comité #1 (Momento sin agotamiento) y Comité #2 (Absorción compradora > vendedora)
    red_vol = pd.Series(np.where(close < open_s, vol_s, np.nan), index=df.index).shift(1).rolling(5, min_periods=1).mean().fillna(0.0)
    green_vol = pd.Series(np.where(close > open_s, vol_s, np.nan), index=df.index).rolling(3, min_periods=1).mean().fillna(vol_s)
    committee_ok = (df["rsi_14"] <= float(params.get("max_rsi_15m_entry", 70.0))) & ((red_vol == 0.0) | (green_vol >= red_vol))

    df["signal_buy"] = trick1_tide & trick2_pullback & trick3_ignition & committee_ok
    df["setup_archetype"] = np.where(df["signal_buy"], "THREE_TRICKS_IGNITION", "NONE")
    df["pred_edge"] = (df["rvol_20"].clip(0.5, 3.0) * 15.0) + (df["clv"] * 25.0) + (df["di_spread"].clip(0.0, 25.0))
    return df


def run_full_auto_backtest(
    initial_capital: float = 5000.0,
    max_risk_pct: float = 0.0028,
    atr_sl_mult: float = 1.25,
    tp_rr: float = 1.9,
    commission_per_order: float = 1.0,
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
    spy_bench_df = benchmark_dfs.get("SPY")
    for t in all_tickers:
        try:
            tk = "XYZ" if t == "SQ" else t
            if tk not in raw.columns.get_level_values(0):
                continue
            df_t = raw[tk].dropna(subset=["Open", "High", "Low", "Close"])
            if len(df_t) < 220:
                continue
            df_t = evaluate_bar_signals(df_t, params, spy_bench_df)
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

    be_trigger_r = float(params.get("breakeven_trigger_r", 0.60))
    be_cushion_usd = float(params.get("breakeven_net_cushion_usd", 2.0))
    t1_trigger_r = float(params.get("trailing_stage1_trigger_r", 1.00))
    t1_lock_mfe_pct = float(params.get("trailing_stage1_lock_mfe_pct", 0.60))
    t2_trigger_r = float(params.get("trailing_stage2_trigger_r", 1.40))
    t2_lock_mfe_pct = float(params.get("trailing_stage2_lock_mfe_pct", 0.75))
    macro_gatekeeper_enabled = bool(params.get("macro_regime_gatekeeper_enabled", True))
    spy_thresh = float(params.get("macro_index_threshold_drop_pct", -0.75))
    qqq_thresh = float(params.get("macro_qqq_threshold_drop_pct", -1.00))
    vwap_filter = bool(params.get("macro_intraday_vwap_filter", True))
    min_breadth_ratio = float(params.get("macro_market_breadth_min_advancing_pct", 0.38))

    macro_vetoed_days = 0
    macro_vetoed_signals = 0

    warmup_days = 200
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
            pos["peak_price"] = max(pos.get("peak_price", entry_p), high_p)
            mfe_ps = max(0.0, pos["peak_price"] - entry_p)

            exit_price = close_p
            exit_reason = ""

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
                peak_r = mfe_ps / risk_dist if risk_dist > 0 else 0.0
                if peak_r >= t2_trigger_r:
                    pos["stage"] = max(pos["stage"], 3)
                    pos["stop_loss"] = max(pos["stop_loss"], round(entry_p + (mfe_ps * t2_lock_mfe_pct), 2))
                elif peak_r >= t1_trigger_r:
                    pos["stage"] = max(pos["stage"], 2)
                    pos["stop_loss"] = max(pos["stop_loss"], round(entry_p + (mfe_ps * t1_lock_mfe_pct), 2))
                elif peak_r >= be_trigger_r:
                    pos["stage"] = max(pos["stage"], 1)
                    be_plus = round(entry_p + (((commission_per_order * 2.0) + be_cushion_usd) / max(1, shares)), 2)
                    pos["stop_loss"] = max(pos["stop_loss"], be_plus)

                if pos["bars_held"] >= 10:
                    exit_price = close_p
                    exit_reason = "TIME_EXPIRY_10D"
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
                "setup_archetype": pos.get("setup_archetype", "THREE_TRICKS_IGNITION"),
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
                for ticker, df_t in ticker_dfs.items():
                    if ticker not in open_positions and current_date in df_t.index:
                        if bool(df_t.loc[current_date].get("signal_buy", False)):
                            macro_vetoed_signals += 1
            else:
                candidates = []
                bt_params = {
                    **params,
                    "max_portfolio_risk_per_trade_pct": max_risk_pct,
                    "atr_stop_loss_multiplier": atr_sl_mult,
                    "take_profit_reward_risk_ratio": tp_rr,
                    "commission_per_order": commission_per_order,
                }
                for ticker, df_t in ticker_dfs.items():
                    if ticker in open_positions or current_date not in df_t.index:
                        continue
                    row = df_t.loc[current_date]
                    if bool(row.get("signal_buy", False)):
                        close_p = float(row["Close"])
                        atr_v = float(row["atr_14"])
                        if atr_v <= 0:
                            continue
                        pb_low = float(row.get("pullback_low") or 0.0)
                        swing_h = float(row.get("recent_swing_high") or 0.0)
                        brackets = trigger_agent.calculate_bracket_levels(
                            entry_price=close_p,
                            atr_14=atr_v,
                            strategy_params=bt_params,
                            recent_swing_high=swing_h if swing_h > close_p else None,
                            pullback_low=pb_low if pb_low > 0 else None,
                        )
                        sizing = trigger_agent.calculate_position_sizing(
                            portfolio_value=portfolio_value,
                            entry_price=close_p,
                            stop_loss_price=brackets["stop_loss_price"],
                            take_profit_price=brackets["take_profit_price"],
                            strategy_params=bt_params,
                            commission_per_order=commission_per_order,
                        )
                        if sizing["viable"]:
                            shares = sizing["shares_to_buy"]
                            capital_needed = (shares * close_p) + commission_per_order
                            if cash >= capital_needed:
                                candidates.append({
                                    "ticker": ticker,
                                    "cluster_id": ticker_to_cluster.get(ticker, "UNKNOWN"),
                                    "setup_archetype": str(row.get("setup_archetype", "THREE_TRICKS_IGNITION")),
                                    "close": close_p,
                                    "sl": brackets["stop_loss_price"],
                                    "tp": brackets["take_profit_price"],
                                    "risk_dist": brackets["risk_per_share"],
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
                            "setup_archetype": cand["setup_archetype"],
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
            "setup_archetype": pos.get("setup_archetype", "HAMMER_ABSORPTION_MA"),
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

    # Estadísticas Globales (Separando Ganancias Reales de Cierres en Breakeven)
    pnls = [t["net_pnl_usd"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    breakeven_trades = [t for t in closed_trades if t["exit_reason"] == "BREAKEVEN_STOP_HIT"]
    strict_wins = [t for t in closed_trades if t["net_pnl_usd"] > 0 and t["exit_reason"] != "BREAKEVEN_STOP_HIT"]
    total_trades = len(closed_trades)
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    strict_win_rate = (len(strict_wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    non_scratch_denom = len(strict_wins) + len(losses)
    core_win_rate_ex_be = (len(strict_wins) / non_scratch_denom * 100.0) if non_scratch_denom > 0 else 0.0
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

    # Desglose por Arquetipo de Vela y Volumen VSA
    by_archetype = {}
    for t in closed_trades:
        arch = t.get("setup_archetype", "UNKNOWN")
        a_stats = by_archetype.setdefault(arch, {"trades": 0, "wins": 0, "strict_wins": 0, "breakevens": 0, "losses": 0, "pnl": 0.0})
        a_stats["trades"] += 1
        if t["net_pnl_usd"] > 0:
            a_stats["wins"] += 1
            if t["exit_reason"] == "BREAKEVEN_STOP_HIT":
                a_stats["breakevens"] += 1
            else:
                a_stats["strict_wins"] += 1
        else:
            a_stats["losses"] += 1
        a_stats["pnl"] = round(a_stats["pnl"] + t["net_pnl_usd"], 2)

    for arch, s_arch in by_archetype.items():
        s_arch["win_rate_pct"] = round((s_arch["wins"] / s_arch["trades"]) * 100.0, 1) if s_arch["trades"] > 0 else 0.0
        denom_a = s_arch["strict_wins"] + s_arch["losses"]
        s_arch["core_win_rate_ex_be_pct"] = round((s_arch["strict_wins"] / denom_a) * 100.0, 1) if denom_a > 0 else 0.0

    # Desglose por Clúster (meramente informativo/observacional; el motor no usa el clúster para decidir)
    by_cluster = {}
    for t in closed_trades:
        cid = t["cluster_id"]
        c_stats = by_cluster.setdefault(cid, {"trades": 0, "wins": 0, "strict_wins": 0, "breakevens": 0, "pnl": 0.0})
        c_stats["trades"] += 1
        if t["net_pnl_usd"] > 0:
            c_stats["wins"] += 1
            if t["exit_reason"] == "BREAKEVEN_STOP_HIT":
                c_stats["breakevens"] += 1
            else:
                c_stats["strict_wins"] += 1
        c_stats["pnl"] = round(c_stats["pnl"] + t["net_pnl_usd"], 2)

    for cid, s in by_cluster.items():
        s["win_rate_pct"] = round((s["wins"] / s["trades"]) * 100.0, 1) if s["trades"] > 0 else 0.0
        s["strict_win_rate_pct"] = round((s["strict_wins"] / s["trades"]) * 100.0, 1) if s["trades"] > 0 else 0.0

    exit_counts = {}
    for t in closed_trades:
        r = t["exit_reason"]
        exit_counts[r] = exit_counts.get(r, 0) + 1

    # Resumen específico del último 1 año (últimas 252 sesiones)
    cutoff_1y_str = sim_dates[-min(252, len(sim_dates))].strftime("%Y-%m-%d")
    trades_1y = [t for t in closed_trades if t["entry_date"] >= cutoff_1y_str]
    wins_1y = [t for t in trades_1y if t["net_pnl_usd"] > 0]
    strict_1y = [t for t in trades_1y if t["net_pnl_usd"] > 0 and t["exit_reason"] != "BREAKEVEN_STOP_HIT"]
    be_1y = [t for t in trades_1y if t["exit_reason"] == "BREAKEVEN_STOP_HIT"]
    loss_1y = [t for t in trades_1y if t["net_pnl_usd"] <= 0]
    gw_1y = sum(t["net_pnl_usd"] for t in wins_1y)
    gl_1y = abs(sum(t["net_pnl_usd"] for t in loss_1y))
    pf_1y = (gw_1y / gl_1y) if gl_1y > 0 else (99.0 if gw_1y > 0 else 0.0)
    denom_1y = len(strict_1y) + len(loss_1y)
    last_1y_summary = {
        "start_date": cutoff_1y_str,
        "end_date": sim_dates[-1].strftime("%Y-%m-%d"),
        "total_closed_trades": len(trades_1y),
        "strict_winning_trades": len(strict_1y),
        "breakeven_scratches": len(be_1y),
        "losing_trades": len(loss_1y),
        "win_rate_pct": round((len(wins_1y) / len(trades_1y) * 100.0), 1) if trades_1y else 0.0,
        "core_win_rate_ex_breakeven_pct": round((len(strict_1y) / denom_1y * 100.0), 1) if denom_1y > 0 else 0.0,
        "profit_factor": round(pf_1y, 2),
        "net_profit_usd": round(sum(t["net_pnl_usd"] for t in trades_1y), 2),
    }

    report = {
        "summary": {
            "initial_capital_usd": initial_capital,
            "final_equity_usd": round(final_equity, 2),
            "net_profit_usd": round(net_profit, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_closed_trades": total_trades,
            "winning_trades": len(wins),
            "strict_winning_trades": len(strict_wins),
            "breakeven_scratches": len(breakeven_trades),
            "losing_trades": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "strict_win_rate_pct": round(strict_win_rate, 1),
            "core_win_rate_ex_breakeven_pct": round(core_win_rate_ex_be, 1),
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
        "last_1y_summary": last_1y_summary,
        "archetype_breakdown": by_archetype,
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
    s1y = rep["last_1y_summary"]
    print("\n" + "=" * 72)
    print("RESULTADOS BACKTEST INSTITUCIONAL V34 (TRULLÀS / WYCKOFF VSA / WEINSTEIN)")
    print("=" * 72)
    print(f"Capital Inicial:     ${s['initial_capital_usd']:,.2f}")
    print(f"Capital Final (3Y):  ${s['final_equity_usd']:,.2f} ({s['total_return_pct']:+.2f}%)")
    print(f"Beneficio Neto (3Y): ${s['net_profit_usd']:+,.2f}")
    print(f"Total Operaciones:   {s['total_closed_trades']}")
    print(
        f"Acierto Core (ex-BE): {s['core_win_rate_ex_breakeven_pct']:.1f}% ({s['strict_winning_trades']}W Reales | "
        f"{s['breakeven_scratches']} Breakeven | {s['losing_trades']}L)"
    )
    print(f"Acierto Nominal:     {s['win_rate_pct']:.1f}% (incluyendo Breakevens como >$0)")
    print(f"Profit Factor (3Y):  {s['profit_factor']:.2f}")
    print(f"Esperanza Media:     {s['avg_r_multiple']:+.2f} R (${s['avg_trade_pnl_usd']:+.2f}/trade)")
    print(f"Max Drawdown (3Y):   {s['max_drawdown_pct']:.2f}% (-${s['max_drawdown_usd']:,.2f})")
    print(f"Cortafuegos -4%:     {'ACTIVADO' if s['circuit_breaker_triggered'] else 'NO ACTIVADO (Protegido)'}")
    print(f"Macro Gatekeeper:    {s['macro_vetoed_days']} sesiones en Veto | {s['macro_vetoed_signals']} señales tóxicas bloqueadas")
    print("-" * 72)
    print(
        f"VENTANA ÚLTIMO AÑO ({s1y['start_date']} -> {s1y['end_date']}): "
        f"{s1y['total_closed_trades']} trades | {s1y['strict_winning_trades']}W / {s1y['breakeven_scratches']}BE / {s1y['losing_trades']}L | "
        f"Core WR: {s1y['core_win_rate_ex_breakeven_pct']:.1f}% (Nominal: {s1y['win_rate_pct']:.1f}%) | "
        f"PF: {s1y['profit_factor']:.2f} | PnL: ${s1y['net_profit_usd']:+,.2f}"
    )
    print("=" * 72)
    print("\nDesglose por Arquetipo de Vela y Volumen VSA (3 Años):")
    for arch, st in rep["archetype_breakdown"].items():
        print(
            f"• {arch:24s}: {st['trades']:2d} trades ({st['strict_wins']}W / {st['breakevens']}BE / {st['losses']}L) | "
            f"Core WR: {st['core_win_rate_ex_be_pct']:5.1f}% | Nominal WR: {st['win_rate_pct']:5.1f}% | PnL: ${st['pnl']:+8.2f}"
        )
    print("\nDesglose por Motivo de Salida:")
    for er, cnt in rep["exit_reasons"].items():
        pct = (cnt / s["total_closed_trades"] * 100.0) if s["total_closed_trades"] > 0 else 0.0
        print(f"• {er:22s}: {cnt:2d} trades ({pct:4.1f}%)")
    print("\nDesglose Observacional por Clúster (Sin reglas por clúster):")
    for cid, st in rep["cluster_breakdown"].items():
        print(
            f"• {cid:22s}: {st['trades']:2d} trades | Win Real: {st['strict_win_rate_pct']:5.1f}% "
            f"(BE: {st['breakevens']}) | PnL: ${st['pnl']:+8.2f}"
        )
