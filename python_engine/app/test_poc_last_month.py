"""
Testing de Concepto (PoC) Cuantitativo: Último Mes de Mercado (2026-09-21 a 2026-10-19)
Valida el funcionamiento riguroso del modelo algorítmico agéntico:
- Descarga histórica suficiente (1 año) para estabilización exacta de SMA200/50, EMA20, ATR14, ADX14, ER10, Sharpe20.
- Ejecución cronológica sesión por sesión del último mes de negociación (21 sesiones).
- Filtro Macro Regime Gatekeeper (SPY / QQQ / Amplitud de mercado).
- Disparadores técnicos vectorizados agnósticos y Esperanza Matemática Compuesta (pred_edge).
- Sizing dinámico con riesgo 1%, multiplicador ATR endógeno y límite de asignación.
- Ratchets de protección de beneficios (Breakeven +0.85R -> +0.08R, Trailing +1.45R -> +0.55R).
- Liquidación intrabarra conservadora y mark-to-market de posiciones vivas al cierre del periodo.
"""
import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

# Configurar entorno de importación
sys.path.insert(0, str(Path(__file__).parent))
from audit_agent import AUDIT_DIR, DEFAULT_CLUSTERS, audit_agent, get_adaptive_allocation_pct
from backtest_engine import evaluate_bar_signals

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("poc-tester")


def run_last_month_poc(
    initial_capital: float = 5000.0,
    max_risk_pct: float = 0.01,
    atr_sl_mult: float = 1.5,
    tp_rr: float = 2.0,
    commission_per_order: float = 0.35,
    max_concurrent_positions: int = 4,
    days_back: int = 30,
) -> dict[str, Any]:
    params = audit_agent.strategy_params
    clusters = params.get("clusters") or DEFAULT_CLUSTERS
    all_tickers = []
    ticker_to_cluster = {}
    for c_id, t_list in clusters.items():
        for t in t_list:
            if t not in all_tickers:
                all_tickers.append(t)
                ticker_to_cluster[t] = c_id

    logger.info(f"[PoC Último Mes] Descargando datos para {len(all_tickers)} activos + benchmarks SPY/QQQ...")
    clean_all = [t if t != "SQ" else "XYZ" for t in all_tickers]
    download_list = list(dict.fromkeys(clean_all + ["SPY", "QQQ"]))

    raw = yf.download(
        download_list,
        period="1y",
        interval="1d",
        group_by="ticker",
        threads=True,
        progress=False,
    )

    # 1. Benchmarks Macro
    benchmark_dfs: dict[str, pd.DataFrame] = {}
    for bm in ("SPY", "QQQ"):
        try:
            if bm in raw.columns.get_level_values(0):
                dfb = raw[bm].dropna(subset=["Open", "High", "Low", "Close"]).copy()
                dfb["ret_1d_pct"] = dfb["Close"].pct_change(1) * 100.0
                dfb["intraday_chg_pct"] = ((dfb["Close"] - dfb["Open"]) / dfb["Open"].replace(0, np.nan)) * 100.0
                dfb["typ_vwap"] = (dfb["High"] + dfb["Low"] + dfb["Close"]) / 3.0
                dfb["above_vwap"] = dfb["Close"] >= (dfb["typ_vwap"] * 0.999)
                benchmark_dfs[bm] = dfb
        except Exception as exc:
            logger.debug(f"Error procesando benchmark {bm}: {exc}")

    # 2. Series de Tickers e Indicadores
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
    if not common_dates:
        raise RuntimeError("No se obtuvieron fechas válidas de mercado.")

    last_market_date = common_dates[-1]
    cutoff_date = last_market_date - timedelta(days=days_back)
    sim_dates = [d for d in common_dates if d >= cutoff_date]

    logger.info(
        f"[PoC Último Mes] Ventana de prueba: {len(sim_dates)} sesiones diarias desde "
        f"{sim_dates[0].strftime('%Y-%m-%d')} hasta {sim_dates[-1].strftime('%Y-%m-%d')}."
    )

    # Parámetros del modelo
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

    portfolio_value = initial_capital
    cash = initial_capital
    open_positions: dict[str, dict[str, Any]] = {}
    closed_trades: list[dict[str, Any]] = []
    daily_journal: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    macro_vetoed_days_count = 0
    total_signals_detected = 0
    total_signals_vetoed = 0
    circuit_breaker_triggered = False

    for current_date in sim_dates:
        date_str = current_date.strftime("%Y-%m-%d")
        session_events = []

        # -------------------------------------------------------------
        # 1. EVALUAR Y GESTIONAR POSICIONES ABIERTAS (Stop, TP, Ratchets)
        # -------------------------------------------------------------
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

            # Evaluación conservadora intrabarra: Stop vigente antes de subir ratchet
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
                # Ratchets progresivos
                if high_p >= entry_p + (t1_trigger_r * risk_dist):
                    if pos["stage"] < 2:
                        session_events.append(f"[{ticker}] Ratchet Trailing activado: SL elevado a ${entry_p + (t1_lock_r * risk_dist):.2f} (+{t1_lock_r}R)")
                    pos["stage"] = max(pos["stage"], 2)
                    pos["stop_loss"] = max(pos["stop_loss"], round(entry_p + (t1_lock_r * risk_dist), 2))
                elif high_p >= entry_p + (be_trigger_r * risk_dist):
                    if pos["stage"] < 1:
                        session_events.append(f"[{ticker}] Breakeven activado: SL elevado a ${entry_p + (be_lock_r * risk_dist):.2f} (+{be_lock_r}R)")
                    pos["stage"] = max(pos["stage"], 1)
                    pos["stop_loss"] = max(pos["stop_loss"], round(entry_p + (be_lock_r * risk_dist), 2))

                if pos["bars_held"] >= 15:
                    exit_price = close_p
                    exit_reason = "TIME_EXPIRY_15D"
                else:
                    pos["bars_held"] += 1
                    pos["current_price"] = close_p
                    continue

            # Liquidación efectiva de la posición cerrada
            gross_pnl = (exit_price - entry_p) * shares
            commissions = commission_per_order * 2.0
            net_pnl = gross_pnl - commissions
            cash += (shares * exit_price) - commission_per_order
            portfolio_value += net_pnl

            r_mult = round(net_pnl / (risk_dist * shares), 2) if risk_dist > 0 else 0.0
            trade_record = {
                "ticker": ticker,
                "cluster_id": pos["cluster_id"],
                "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                "exit_date": date_str,
                "bars_held": pos["bars_held"],
                "entry_price": round(entry_p, 2),
                "exit_price": round(exit_price, 2),
                "shares": shares,
                "stop_loss": sl,
                "take_profit": tp,
                "gross_pnl_usd": round(gross_pnl, 2),
                "commissions_usd": round(commissions, 2),
                "net_pnl_usd": round(net_pnl, 2),
                "net_pnl_pct": round((net_pnl / pos["capital_invested"]) * 100.0, 2),
                "exit_reason": exit_reason,
                "r_multiple": r_mult,
            }
            closed_trades.append(trade_record)
            closed_today.append(ticker)
            session_events.append(f"[{ticker}] Cierre {exit_reason} @ ${exit_price:.2f} | PnL: ${net_pnl:+.2f} ({r_mult:+.2f}R)")

        for t in closed_today:
            del open_positions[t]

        # -------------------------------------------------------------
        # 2. CORTAFUEGOS DE CARTERA (-4%)
        # -------------------------------------------------------------
        drawdown_pct = ((initial_capital - portfolio_value) / initial_capital)
        if drawdown_pct >= 0.04 and not circuit_breaker_triggered:
            circuit_breaker_triggered = True
            session_events.append(f"¡CORTAFUEGOS -4% ACTIVADO! Drawdown {drawdown_pct*100:.2f}%")

        # -------------------------------------------------------------
        # 3. MACRO REGIME GATEKEEPER
        # -------------------------------------------------------------
        macro_veto_today = False
        spy_ret = 0.0
        qqq_ret = 0.0
        adv_ratio = 0.50

        if macro_gatekeeper_enabled:
            spy_daily = 0.0
            spy_intraday = 0.0
            spy_above_vwap = True
            qqq_daily = 0.0
            qqq_intraday = 0.0
            qqq_above_vwap = True

            if "SPY" in benchmark_dfs and current_date in benchmark_dfs["SPY"].index:
                s_row = benchmark_dfs["SPY"].loc[current_date]
                spy_daily = float(s_row.get("ret_1d_pct", 0.0))
                spy_intraday = float(s_row.get("intraday_chg_pct", 0.0))
                spy_above_vwap = bool(s_row.get("above_vwap", True))
                spy_ret = spy_daily

            if "QQQ" in benchmark_dfs and current_date in benchmark_dfs["QQQ"].index:
                q_row = benchmark_dfs["QQQ"].loc[current_date]
                qqq_daily = float(q_row.get("ret_1d_pct", 0.0))
                qqq_intraday = float(q_row.get("intraday_chg_pct", 0.0))
                qqq_above_vwap = bool(q_row.get("above_vwap", True))
                qqq_ret = qqq_daily

            # Amplitud del universo de activos
            adv_count = sum(
                1 for df_t in ticker_dfs.values()
                if current_date in df_t.index and float(df_t.loc[current_date].get("ret_1d_pct", 0.0)) > 0.0
            )
            tot_count = sum(1 for df_t in ticker_dfs.values() if current_date in df_t.index)
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
                macro_vetoed_days_count += 1
                macro_status = "RISK_OFF_VETO"
            elif spy_daily <= -0.40 or qqq_daily <= -0.60 or adv_ratio < 0.45:
                macro_status = "CAUTION"
            else:
                macro_status = "RISK_ON"
        else:
            macro_status = "DISABLED"

        # -------------------------------------------------------------
        # 4. DETECCIÓN DE SEÑALES Y DISPARADORES CUANTITATIVOS
        # -------------------------------------------------------------
        today_raw_signals = []
        for ticker, df_t in ticker_dfs.items():
            if current_date not in df_t.index:
                continue
            row = df_t.loc[current_date]
            if bool(row.get("signal_buy", False)):
                today_raw_signals.append({
                    "ticker": ticker,
                    "cluster_id": ticker_to_cluster.get(ticker, "UNKNOWN"),
                    "close": float(row["Close"]),
                    "atr_pct": float(row["atr_pct"]),
                    "atr_14": float(row["atr_14"]),
                    "adx": float(row["adx"]),
                    "stoch_k": float(row["stoch_k"]),
                    "stoch_d": float(row["stoch_d"]),
                    "rsi": float(row["rsi_14"]),
                    "er_10": float(row["er_10"]),
                    "pred_edge": float(row.get("pred_edge", 25.0)),
                })

        total_signals_detected += len(today_raw_signals)

        # -------------------------------------------------------------
        # 5. ENRUTAMIENTO Y EJECUCIÓN DE NUEVAS ÓRDENES
        # -------------------------------------------------------------
        new_entries = []
        if macro_veto_today:
            total_signals_vetoed += len(today_raw_signals)
            if today_raw_signals:
                vetoed_list = [s["ticker"] for s in today_raw_signals]
                session_events.append(
                    f"[MACRO VETO] Se bloquearon {len(vetoed_list)} señales ({', '.join(vetoed_list)}) "
                    f"por caída de índices (SPY {spy_ret:+.2f}%, QQQ {qqq_ret:+.2f}%, Amplitud {adv_ratio*100:.1f}%)"
                )
        elif not circuit_breaker_triggered and len(open_positions) < max_concurrent_positions:
            candidates = []
            for sig in today_raw_signals:
                ticker = sig["ticker"]
                if ticker in open_positions:
                    continue

                close_p = sig["close"]
                atr_v = sig["atr_14"]
                atr_p = sig["atr_pct"]
                if atr_v <= 0:
                    continue

                if endogenous_scaling:
                    ticker_atr_mult = min(2.0, max(1.5, atr_sl_mult + 0.12 * (atr_p - 1.8)))
                else:
                    ticker_atr_mult = atr_sl_mult

                sl_p = round(close_p - (ticker_atr_mult * atr_v), 2)
                risk_dist = close_p - sl_p
                if risk_dist <= 0:
                    continue
                tp_p = round(close_p + (tp_rr * risk_dist), 2)

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
                            "cluster_id": sig["cluster_id"],
                            "close": close_p,
                            "sl": sl_p,
                            "tp": tp_p,
                            "risk_dist": risk_dist,
                            "shares": shares,
                            "capital_needed": capital_needed,
                            "pred_edge": sig["pred_edge"],
                            "atr_mult": round(ticker_atr_mult, 2),
                        })

            # Ordenar por Esperanza Matemática (pred_edge)
            candidates.sort(key=lambda c: c["pred_edge"], reverse=True)
            slots_avail = max_concurrent_positions - len(open_positions)

            for cand in candidates[:slots_avail]:
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
                    new_entries.append(cand["ticker"])
                    session_events.append(
                        f"[{cand['ticker']}] Entrada aprobada: {cand['shares']} accs @ ${cand['close']:.2f} "
                        f"(SL: ${cand['sl']:.2f}, TP: ${cand['tp']:.2f}, Edge: {cand['pred_edge']:.1f})"
                    )

        # -------------------------------------------------------------
        # 6. VALORACIÓN DE CARTERA (MARK TO MARKET)
        # -------------------------------------------------------------
        total_open_value = 0.0
        for ticker, pos in open_positions.items():
            df_t = ticker_dfs.get(ticker)
            if df_t is not None and current_date in df_t.index:
                pos["current_price"] = float(df_t.loc[current_date]["Close"])
            total_open_value += pos["shares"] * pos["current_price"]

        total_equity = cash + total_open_value

        equity_curve.append({
            "date": date_str,
            "equity": round(total_equity, 2),
            "cash": round(cash, 2),
            "open_positions": len(open_positions),
            "spy_return_pct": round(spy_ret, 2),
            "qqq_return_pct": round(qqq_ret, 2),
        })

        daily_journal.append({
            "date": date_str,
            "macro_status": macro_status,
            "spy_ret_pct": round(spy_ret, 2),
            "qqq_ret_pct": round(qqq_ret, 2),
            "advancing_ratio_pct": round(adv_ratio * 100.0, 1),
            "raw_signals_count": len(today_raw_signals),
            "raw_signal_tickers": [s["ticker"] for s in today_raw_signals],
            "new_entries": new_entries,
            "open_positions_count": len(open_positions),
            "equity": round(total_equity, 2),
            "events": session_events,
        })

    # -------------------------------------------------------------
    # 7. ESTADÍSTICAS FINALES Y MARK-TO-MARKET DE POSICIONES ACTIVAS
    # -------------------------------------------------------------
    unrealized_pnl = 0.0
    active_positions_report = []
    for ticker, pos in open_positions.items():
        u_pnl = (pos["current_price"] - pos["entry_price"]) * pos["shares"]
        unrealized_pnl += u_pnl
        active_positions_report.append({
            "ticker": ticker,
            "cluster_id": pos["cluster_id"],
            "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
            "entry_price": pos["entry_price"],
            "current_price": round(pos["current_price"], 2),
            "shares": pos["shares"],
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
            "stage": pos["stage"],
            "unrealized_pnl_usd": round(u_pnl, 2),
            "unrealized_pnl_pct": round((u_pnl / pos["capital_invested"]) * 100.0, 2),
            "bars_held": pos["bars_held"],
        })

    wins = [t for t in closed_trades if t["net_pnl_usd"] > 0]
    losses = [t for t in closed_trades if t["net_pnl_usd"] <= 0]
    realized_net_pnl = sum(t["net_pnl_usd"] for t in closed_trades)
    final_equity = portfolio_value + unrealized_pnl
    total_net_pnl = final_equity - initial_capital
    total_return_pct = (total_net_pnl / initial_capital) * 100.0

    win_rate = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    gross_profits = sum(t["net_pnl_usd"] for t in wins)
    gross_losses = abs(sum(t["net_pnl_usd"] for t in losses))
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (99.0 if gross_profits > 0 else 0.0)

    # Cálculo de Drawdown histórico de la curva de equity en el mes
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

    # Desglose de motivos de salida
    exit_counts = {}
    for t in closed_trades:
        r = t["exit_reason"]
        exit_counts[r] = exit_counts.get(r, 0) + 1

    # Desglose por clúster
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

    report = {
        "period": {
            "start_date": sim_dates[0].strftime("%Y-%m-%d"),
            "end_date": sim_dates[-1].strftime("%Y-%m-%d"),
            "trading_sessions": len(sim_dates),
        },
        "summary": {
            "initial_capital_usd": initial_capital,
            "final_equity_usd": round(final_equity, 2),
            "realized_net_pnl_usd": round(realized_net_pnl, 2),
            "unrealized_pnl_usd": round(unrealized_pnl, 2),
            "total_net_profit_usd": round(total_net_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_closed_trades": len(closed_trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "avg_trade_pnl_usd": round(realized_net_pnl / len(closed_trades), 2) if closed_trades else 0.0,
            "avg_r_multiple": round(sum(t["r_multiple"] for t in closed_trades) / len(closed_trades), 2) if closed_trades else 0.0,
            "circuit_breaker_triggered": circuit_breaker_triggered,
            "macro_vetoed_days": macro_vetoed_days_count,
            "total_signals_detected": total_signals_detected,
            "total_signals_vetoed": total_signals_vetoed,
        },
        "exit_reasons": exit_counts,
        "cluster_breakdown": by_cluster,
        "active_open_positions": active_positions_report,
        "closed_trades": closed_trades,
        "equity_curve": equity_curve,
        "daily_journal": daily_journal,
    }

    out_file = AUDIT_DIR / "poc_last_month_results.json"
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[PoC Último Mes] Resultados guardados en {out_file}")

    return report


if __name__ == "__main__":
    rep = run_last_month_poc()
    s = rep["summary"]
    p = rep["period"]
    print("\n" + "=" * 70)
    print("INFORME DE TESTING DE CONCEPTO: ÚLTIMO MES DE MERCADO")
    print(f"Período: {p['start_date']} al {p['end_date']} ({p['trading_sessions']} sesiones)")
    print("=" * 70)
    print(f"Capital Inicial:         ${s['initial_capital_usd']:,.2f}")
    print(f"Capital Final Total:     ${s['final_equity_usd']:,.2f} ({s['total_return_pct']:+.2f}%)")
    print(f"Beneficio Realizado:     ${s['realized_net_pnl_usd']:+,.2f}")
    print(f"Beneficio Latente (MTM): ${s['unrealized_pnl_usd']:+,.2f}")
    print(f"Beneficio Neto Total:    ${s['total_net_profit_usd']:+,.2f}")
    print(f"Operaciones Cerradas:    {s['total_closed_trades']}")
    print(f"Ratio de Acierto (Win):  {s['win_rate_pct']:.1f}% ({s['winning_trades']} Ganadoras / {s['losing_trades']} Perdedoras)")
    print(f"Profit Factor:           {s['profit_factor']:.2f}")
    print(f"Esperanza Matemática:    {s['avg_r_multiple']:+.2f} R (${s['avg_trade_pnl_usd']:+.2f}/trade)")
    print(f"Máximo Drawdown Mes:     {s['max_drawdown_pct']:.2f}% (-${s['max_drawdown_usd']:,.2f})")
    print(f"Cortafuegos de Riesgo:   {'DISPARADO' if s['circuit_breaker_triggered'] else 'NO DISPARADO (Riesgo Controlado)'}")
    print(f"Macro Gatekeeper:        {s['macro_vetoed_days']} días en Veto | {s['total_signals_vetoed']}/{s['total_signals_detected']} señales filtradas")
    print("=" * 70)

    print("\nDetalle de Operaciones Cerradas:")
    if rep["closed_trades"]:
        for t in rep["closed_trades"]:
            print(
                f"• {t['ticker']:<5} [{t['cluster_id']:<20}] Entrada: {t['entry_date']} @ ${t['entry_price']:.2f} | "
                f"Salida: {t['exit_date']} @ ${t['exit_price']:.2f} ({t['exit_reason']:<18}) | "
                f"PnL: ${t['net_pnl_usd']:+6.2f} ({t['r_multiple']:+5.2f}R)"
            )
    else:
        print("• No se cerraron operaciones durante la ventana (o continúan abiertas).")

    print("\nPosiciones Activas al Cierre del Mes (Mark to Market):")
    if rep["active_open_positions"]:
        for pos in rep["active_open_positions"]:
            print(
                f"• {pos['ticker']:<5} Entrada: {pos['entry_date']} @ ${pos['entry_price']:.2f} | "
                f"Precio Actual: ${pos['current_price']:.2f} | SL: ${pos['stop_loss']:.2f} | "
                f"PnL Latente: ${pos['unrealized_pnl_usd']:+6.2f} ({pos['unrealized_pnl_pct']:+5.2f}%) | "
                f"Etapa Ratchet: {pos['stage']}"
            )
    else:
        print("• 0 posiciones abiertas al cierre del mes.")

    print("\nDesglose de Motivos de Salida:")
    for er, cnt in rep["exit_reasons"].items():
        print(f"• {er:<22}: {cnt} trades")

    print("\nDesglose por Clúster:")
    for cid, st in rep["cluster_breakdown"].items():
        print(f"• {cid:<22}: {st['trades']} trades | Win: {st['win_rate_pct']:5.1f}% | PnL: ${st['pnl']:+8.2f}")
