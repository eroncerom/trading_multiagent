import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import ai_advisor
from audit_agent import DEFAULT_STRATEGY_PARAMS
from position_guardian import evaluate_position_guardian, extract_intraday_guardian_signals
from three_tricks_engine import (
    _fetch_alpaca_intraday_batch,
    evaluate_daily_structure,
    evaluate_three_tricks,
    extract_intraday_features,
)
from trigger_agent import TriggerExecutionAgent, ZeroLossSignalBus

NY_TZ = ZoneInfo("America/New_York")


def _make_daily_df(n: int = 230, bullish: bool = True, falling_knife: bool = False) -> pd.DataFrame:
    dates = pd.date_range("2025-11-01", periods=n, freq="B", tz="UTC")
    base = 100.0 + np.linspace(0.0, 45.0 if bullish else -35.0, n)
    opens = base - (0.3 if bullish else -0.3)
    closes = base + (0.6 if bullish else -0.6)
    highs = np.maximum(opens, closes) + 0.8
    lows = np.minimum(opens, closes) - 0.6
    vols = np.full(n, 2_000_000.0)
    if falling_knife:
        opens[-1] = closes[-2]
        closes[-1] = opens[-1] - 5.5
        highs[-1] = opens[-1] + 0.1
        lows[-1] = closes[-1] - 0.2
        vols[-1] = 4_500_000.0
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=dates,
    )


def _make_intraday_15m_pullback_ignition(
    overextended: bool = False,
    red_volume_heavy: bool = False,
    high_rsi: bool = False,
) -> pd.DataFrame:
    # 2 sesiones previas + sesión actual (30 barras totales de 15m)
    idx_prev = pd.date_range("2026-04-08 09:30", periods=18, freq="15min", tz=NY_TZ)
    idx_curr = pd.date_range("2026-04-09 09:30", periods=16, freq="15min", tz=NY_TZ)
    idx = idx_prev.append(idx_curr).tz_convert("UTC")

    n = len(idx)
    prices = np.full(n, 144.5)
    for i in range(n):
        if i < 18:
            prices[i] = 143.6 + i * 0.06
        elif i < 25:
            prices[i] = 144.7 + (i - 18) * 0.14
        elif i < 32:
            # Pullback controlado directo hacia EMA20_15m (~144.5)
            prices[i] = 145.2 - (i - 25) * 0.12
        else:
            prices[i] = 144.45 + (i - 32) * 0.12

    opens = prices - 0.14
    closes = prices.copy()
    highs = np.maximum(opens, closes) + 0.24
    lows = np.minimum(opens, closes) - 0.22
    vols = np.full(n, 100_000.0)

    # Velas de impulso iniciales verdes
    for i in range(18, 25):
        opens[i] = closes[i] - 0.30
        highs[i] = closes[i] + 0.18
        lows[i] = opens[i] - 0.18
        vols[i] = 160_000.0

    # Velas rojas de pullback en zona de valor
    for i in range(25, n - 1):
        opens[i] = closes[i] + 0.14
        closes[i] = 144.60 + max(0, (31 - i)) * 0.05
        highs[i] = opens[i] + 0.14
        lows[i] = closes[i] - 0.06
        vols[i] = 280_000.0 if red_volume_heavy else 70_000.0

    # Barra previa (n-2): test suave en la zona de valor con techo definido cerca de EMA20_15m
    opens[-2] = 144.65
    closes[-2] = 144.52
    lows[-2] = 144.46
    highs[-2] = 144.70

    if high_rsi:
        # Subida sostenida previa para empujar RSI > 75 y luego micro-pausa en última barra
        for i in range(n - 15, n):
            prices[i] = 144.0 + (i - (n - 15)) * 0.70
            opens[i] = prices[i] - 0.35
            closes[i] = prices[i]
            highs[i] = closes[i] + 0.10
            lows[i] = opens[i] - 0.10
        lows[-2] = lows[-3] - 0.10

    if overextended:
        # 3 velas verdes verticales alejadas de EMA20_15m
        for k in (-3, -2, -1):
            base_v = 148.0 + (k + 3) * 2.2
            opens[k] = base_v
            closes[k] = base_v + 2.0
            highs[k] = closes[k] + 0.15
            lows[k] = opens[k] - 0.05
            vols[k] = 210_000.0
    else:
        # Vela actual de ignición rompiendo prev_high pegada a EMA20_15m con RVOL >= 1.25x
        prev_hi = float(highs[-2])
        opens[-1] = prev_hi - 0.16
        lows[-1] = opens[-1] - 0.03
        closes[-1] = prev_hi + 0.12
        highs[-1] = closes[-1] + 0.03
        vols[-1] = 360_000.0 if red_volume_heavy else 240_000.0

    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


class TestThreeTricksMotor(unittest.TestCase):
    def setUp(self):
        self.trigger = TriggerExecutionAgent(ZeroLossSignalBus())
        self.params = dict(DEFAULT_STRATEGY_PARAMS)

    def test_1_bracket_levels_and_065pct_risk_h4(self):
        brackets = self.trigger.calculate_bracket_levels(
            entry_price=150.00,
            atr_14=3.00,  # 1.8 * 3.00 = 5.40
            strategy_params=self.params,
        )
        self.assertEqual(brackets["risk_per_share"], 5.40)
        self.assertEqual(brackets["stop_loss_price"], 144.60)
        self.assertEqual(brackets["take_profit_price"], 160.80)  # +2.0R
        self.assertEqual(brackets["breakeven_trigger_price"], 156.48)  # +1.20R

        sizing = self.trigger.calculate_position_sizing(
            portfolio_value=5000.0,
            entry_price=150.00,
            stop_loss_price=brackets["stop_loss_price"],
            take_profit_price=brackets["take_profit_price"],
            strategy_params=self.params,
        )
        self.assertTrue(sizing["viable"], msg=sizing.get("rejection_reason"))
        # Riesgo 0.65% de 5000 = $32.50 máx (5 acciones * 5.40 + $2 comm = $29.00)
        self.assertLessEqual(sizing["max_risk_eur"], 32.50)
        self.assertLessEqual(sizing["invested_capital"], 1000.0)  # tope 20% = $1,000
        self.assertGreaterEqual(sizing["expected_profit_eur"], 4.0)

    def test_2_commission_and_min_net_profit_gate(self):
        # Si el potencial neto es < $4.00, debe rechazar
        bad_sizing = self.trigger.calculate_position_sizing(
            portfolio_value=5000.0,
            entry_price=200.00,
            stop_loss_price=197.50,
            take_profit_price=205.00,  # 2.0R; con 1 acción -> $5 bruto - $2 comm = $3.00 neto < $4.00
            strategy_params={**self.params, "max_portfolio_risk_per_trade_pct": 0.0010},
        )
        self.assertFalse(bad_sizing["viable"])
        self.assertIn("neto", bad_sizing["rejection_reason"].lower())

        # Ratio R:R < 1.80R debe rechazarse
        low_rr = self.trigger.calculate_position_sizing(
            portfolio_value=5000.0,
            entry_price=100.00,
            stop_loss_price=97.00,
            take_profit_price=104.00,  # 1.33R < 1.80R
            strategy_params=self.params,
        )
        self.assertFalse(low_rr["viable"])
        self.assertIn("r:r", low_rr["rejection_reason"].lower())

    def test_3_three_tricks_blocks_downtrend_and_falling_knife(self):
        df_down = _make_daily_df(bullish=False)
        d_down = evaluate_daily_structure(df_down)
        self.assertIsNotNone(d_down)
        self.assertFalse(d_down["daily_trend_ok"])

        df_knife = _make_daily_df(bullish=True, falling_knife=True)
        d_knife = evaluate_daily_structure(df_knife)
        self.assertIsNotNone(d_knife)
        self.assertTrue(d_knife["falling_knife"])
        self.assertFalse(d_knife["daily_trend_ok"])

    def test_4_h4_three_rules_pullback_and_15m_turn(self):
        df_d = _make_daily_df(bullish=True)
        # Caso A: tendencia alcista pero SIN pullback (RSI2 alto) -> NO dispara
        daily_no_pb = evaluate_daily_structure(df_d)
        self.assertTrue(daily_no_pb["daily_trend_ok"])
        self.assertFalse(daily_no_pb["pullback_pb10_ok"])

        df_15m_ok = _make_intraday_15m_pullback_ignition()
        intra_ok = extract_intraday_features(df_15m_ok)
        self.assertIsNotNone(intra_ok)
        res_no_pb = evaluate_three_tricks(daily_no_pb, intra_ok, self.params)
        self.assertFalse(res_no_pb["signal_triggered"])
        self.assertIn("Regla 2", res_no_pb["block_reason"])

        # Caso B: tendencia alcista + pullback profundo PB10 (RSI2 <= 10) + giro 15m >= VWAP -> DISPARA
        df_pb = df_d.copy()
        df_pb.iloc[-2, df_pb.columns.get_loc("Close")] = df_pb["Close"].iloc[-3] * 0.985
        df_pb.iloc[-1, df_pb.columns.get_loc("Close")] = df_pb["Close"].iloc[-2] * 0.982
        daily_pb = evaluate_daily_structure(df_pb)
        self.assertTrue(daily_pb["daily_trend_ok"])
        self.assertTrue(daily_pb["pullback_pb10_ok"])

        intra_above = {**intra_ok, "price": round(intra_ok["vwap"] + 0.10, 2)}
        res_ok = evaluate_three_tricks(daily_pb, intra_above, self.params)
        self.assertTrue(res_ok["signal_triggered"], msg=res_ok.get("block_reason"))
        self.assertEqual(res_ok["setup_archetype"], "SWING_PULLBACK_H4")
        self.assertGreaterEqual(res_ok["quant_score"], 75)

        # Caso C: precio bajo VWAP en 15m -> BLOQUEA
        intra_below_vwap = {**intra_ok, "price": intra_ok["vwap"] - 0.50}
        res_vwap = evaluate_three_tricks(daily_pb, intra_below_vwap, self.params)
        self.assertFalse(res_vwap["signal_triggered"])
        self.assertIn("VWAP", res_vwap["block_reason"])

    def test_5_ai_advisor_committee_3_prompt_contract(self):
        self.assertIn("48", ai_advisor.SYSTEM_PROMPT)
        self.assertIn("-DI > +DI", ai_advisor.SYSTEM_PROMPT)
        prompt = ai_advisor.build_user_prompt(
            {
                "ticker": "NVDA",
                "entry_price": 140.0,
                "vwap": 139.2,
                "ema20_15m": 139.4,
                "atr_15m": 0.9,
                "rvol_intraday": 1.6,
                "dist_from_ema20_atr": 0.67,
                "avg_red_pb_vol": 65000,
                "avg_green_imp_vol": 140000,
                "order_sizing": {"stop_loss_price": 138.8, "take_profit_price": 142.3, "shares_to_buy": 10},
                "financial_metrics": {"expected_net_profit": 21.0, "portfolio_risk_pct": 0.28},
            },
            portfolio_value=5000.0,
        )
        parsed = json.loads(prompt.split("JSON):\n", 1)[1])
        self.assertEqual(parsed["motor"], "LOS_3_TRUCOS_INTRADIA")
        self.assertEqual(parsed["vwap_sesion"], 139.2)
        self.assertEqual(parsed["rvol_intradia"], 1.6)

    def test_6_position_guardian_h4_be_plus_rsi2_and_time_exit(self):
        midday_utc = datetime(2026, 4, 9, 17, 0, tzinfo=timezone.utc)  # 13:00 ET
        eod_utc = datetime(2026, 4, 9, 19, 50, tzinfo=timezone.utc)    # 15:50 ET (ventana EOD 15m)

        # A) Sin aborto prematuro bajo VWAP: el SL estructural 1.8x ATR_1D trabaja libremente
        hold_res = evaluate_position_guardian(
            symbol="MSFT",
            qty=5,
            entry_price=150.0,
            current_price=149.20,
            current_sl=144.60,
            current_tp=160.80,
            peak_price=150.10,
            initial_risk_per_share=5.40,
            unrealized_pl=-4.0,
            clv_daily=0.35,
            intraday_sig={"has_intraday": True, "vwap": 149.90, "below_vwap": True, "rvol_intraday": 1.45},
            params=self.params,
            now_utc=midday_utc,
            rsi2_daily=35.0,
            days_held=1.0,
        )
        self.assertEqual(hold_res["action_type"], "NONE")
        self.assertEqual(hold_res["regime_code"], "HOLD_STEADY")

        # B) En +0.60R aún NO activa BE+ (espera a +1.20R)
        no_early_be = evaluate_position_guardian(
            symbol="NVDA",
            qty=5,
            entry_price=150.0,
            current_price=153.24,  # +0.60R
            current_sl=144.60,
            current_tp=160.80,
            peak_price=153.24,
            initial_risk_per_share=5.40,
            unrealized_pl=16.20,
            clv_daily=0.75,
            intraday_sig={},
            params=self.params,
            now_utc=midday_utc,
        )
        self.assertEqual(no_early_be["action_type"], "NONE")

        # C) En +1.20R activa Candado Breakeven+: Entrada + ($2 comm + $3 neto)/5 = $151.00
        be_res = evaluate_position_guardian(
            symbol="NVDA",
            qty=5,
            entry_price=150.0,
            current_price=156.48,  # +1.20R
            current_sl=144.60,
            current_tp=160.80,
            peak_price=156.48,
            initial_risk_per_share=5.40,
            unrealized_pl=32.40,
            clv_daily=0.80,
            intraday_sig={},
            params=self.params,
            now_utc=midday_utc,
        )
        self.assertEqual(be_res["action_type"], "ADJUST_SL")
        self.assertEqual(be_res["regime_code"], "BREAKEVEN_LOCK")
        self.assertEqual(be_res["recommended_sl"], 151.00)

        # D) Ventana EOD NY (15:50 ET): RSI(2)_1D >= 75 y PnL neto > $4 -> RSI2_OVERBOUGHT_EXIT
        rsi_exit = evaluate_position_guardian(
            symbol="AAPL",
            qty=5,
            entry_price=150.0,
            current_price=154.00,
            current_sl=144.60,
            current_tp=160.80,
            peak_price=154.20,
            initial_risk_per_share=5.40,
            unrealized_pl=20.0,
            clv_daily=0.85,
            intraday_sig={},
            params=self.params,
            now_utc=eod_utc,
            rsi2_daily=78.5,
            days_held=4.0,
        )
        self.assertEqual(rsi_exit["action_type"], "CLOSE_IMMEDIATELY")
        self.assertEqual(rsi_exit["regime_code"], "RSI2_OVERBOUGHT_EXIT")

        # E) Ventana EOD NY (15:50 ET): 12 días abierta -> TIME_EXIT_12D
        time_exit = evaluate_position_guardian(
            symbol="AAPL",
            qty=5,
            entry_price=150.0,
            current_price=150.50,
            current_sl=144.60,
            current_tp=160.80,
            peak_price=152.00,
            initial_risk_per_share=5.40,
            unrealized_pl=2.50,
            clv_daily=0.55,
            intraday_sig={},
            params=self.params,
            now_utc=eod_utc,
            rsi2_daily=45.0,
            days_held=12.0,
        )
        self.assertEqual(time_exit["action_type"], "CLOSE_IMMEDIATELY")
        self.assertEqual(time_exit["regime_code"], "TIME_EXIT_12D")

    def test_7_extract_intraday_guardian_signals_handles_df(self):
        idx5 = pd.date_range("2026-04-09 14:00", periods=15, freq="5min", tz="UTC")
        df5 = pd.DataFrame(
            {
                "Open": np.linspace(100, 102, 15),
                "High": np.linspace(100.3, 102.3, 15),
                "Low": np.linspace(99.8, 101.8, 15),
                "Close": np.linspace(100.1, 102.1, 15),
                "Volume": np.full(15, 50_000.0),
            },
            index=idx5,
        )
        sig = extract_intraday_guardian_signals(df5, None)
        self.assertGreater(sig["vwap"], 0.0)
        self.assertGreater(sig["ema9_5m"], 0.0)
        self.assertFalse(sig["below_vwap"])

    def test_8_strategy_params_json_valid_and_clusters_intact(self):
        params_path = Path(__file__).parent / "audit_data" / "strategy_params.json"
        data = json.loads(params_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 51)
        self.assertEqual(data["engine_mode"], "SWING_PULLBACK_H4")
        self.assertEqual(data["max_portfolio_risk_per_trade_pct"], 0.0065)
        self.assertEqual(data["max_concurrent_positions"], 6)
        self.assertEqual(data["commission_per_order"], 1.0)
        self.assertEqual(data["min_net_profit_usd"], 4.0)
        self.assertTrue(data["auto_adapt_positions"])
        self.assertEqual(len(data["clusters"]), 17)

    def test_9_alpaca_intraday_batch_uses_iex_and_fallback(self):
        import os
        import sys
        import types
        from unittest.mock import patch

        recorded_reqs = []
        idx = pd.MultiIndex.from_product(
            [["AAPL", "BRK.B"], pd.date_range("2026-04-09 14:00", periods=3, freq="15min", tz="UTC")],
            names=["symbol", "timestamp"],
        )
        fake_df = pd.DataFrame(
            {
                "open": [100.0, 100.5, 101.0, 400.0, 401.0, 402.0],
                "high": [100.8, 101.2, 101.6, 401.5, 402.5, 403.5],
                "low": [99.9, 100.3, 100.8, 399.5, 400.5, 401.5],
                "close": [100.6, 101.0, 101.4, 401.0, 402.0, 403.0],
                "volume": [10000.0, 12000.0, 15000.0, 5000.0, 6000.0, 7000.0],
            },
            index=idx,
        )

        class _EnumVal(str):
            pass

        fake_enums = types.ModuleType("alpaca.data.enums")
        fake_enums.DataFeed = types.SimpleNamespace(IEX=_EnumVal("iex"), SIP=_EnumVal("sip"))
        fake_enums.Adjustment = types.SimpleNamespace(SPLIT=_EnumVal("split"))

        fake_tf = types.ModuleType("alpaca.data.timeframe")
        fake_tf.TimeFrame = lambda amount, unit: (amount, unit)
        fake_tf.TimeFrameUnit = types.SimpleNamespace(Minute="Minute")

        fake_reqs = types.ModuleType("alpaca.data.requests")
        fake_reqs.StockBarsRequest = lambda **kwargs: dict(kwargs)

        class _FakeClient:
            def __init__(self, key, sec):
                pass

            def get_stock_bars(self, req):
                recorded_reqs.append(req)
                if req.get("feed") == "sip":
                    raise RuntimeError("403 subscription does not permit querying recent SIP data")
                return types.SimpleNamespace(df=fake_df)

        fake_hist = types.ModuleType("alpaca.data.historical")
        fake_hist.StockHistoricalDataClient = _FakeClient

        fake_alpaca = types.ModuleType("alpaca")
        fake_alpaca_data = types.ModuleType("alpaca.data")

        stub_mods = {
            "alpaca": fake_alpaca,
            "alpaca.data": fake_alpaca_data,
            "alpaca.data.enums": fake_enums,
            "alpaca.data.timeframe": fake_tf,
            "alpaca.data.requests": fake_reqs,
            "alpaca.data.historical": fake_hist,
        }

        with patch.dict(sys.modules, stub_mods), patch.dict(
            os.environ, {"ALPACA_API_KEY": "pk_test", "ALPACA_SECRET_KEY": "sk_test", "ALPACA_DATA_FEED": ""}
        ):
            res = _fetch_alpaca_intraday_batch(["AAPL", "BRK-B"], "15m", 5)
            self.assertIn("AAPL", res)
            self.assertIn("BRK-B", res)
            self.assertEqual(recorded_reqs[-1]["symbol_or_symbols"], ["AAPL", "BRK.B"])
            self.assertEqual(recorded_reqs[-1]["feed"], "iex")
            self.assertEqual(recorded_reqs[-1]["adjustment"], "split")

        recorded_reqs.clear()
        with patch.dict(sys.modules, stub_mods), patch.dict(
            os.environ, {"ALPACA_API_KEY": "pk_test", "ALPACA_SECRET_KEY": "sk_test", "ALPACA_DATA_FEED": "sip"}
        ):
            res_fb = _fetch_alpaca_intraday_batch(["AAPL"], "15m", 5)
            self.assertIn("AAPL", res_fb)
            self.assertEqual([r["feed"] for r in recorded_reqs], ["sip", "iex"])


if __name__ == "__main__":
    unittest.main()
