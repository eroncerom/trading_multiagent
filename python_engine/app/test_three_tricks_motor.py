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

    def test_1_bracket_levels_and_028pct_risk_never_40_usd_loss(self):
        brackets = self.trigger.calculate_bracket_levels(
            entry_price=150.00,
            atr_14=3.80,  # ATR diario grande (legacy hubiera dado SL -$8)
            strategy_params=self.params,
            recent_swing_high=153.50,
            atr_15m=0.85,  # ATR intradía realista
            pullback_low=149.10,
        )
        # SL anclado cerca del pullback_low (149.10 - 0.25*0.85 = 148.89), riesgo por acción ~1.11
        self.assertGreater(brackets["stop_loss_price"], 147.00)
        self.assertLess(brackets["stop_loss_price"], 149.50)
        self.assertLessEqual(brackets["risk_per_share"], 1.30)
        # TP realista (1.9R o techo 153.50*0.996 = 152.89), nunca +10%
        self.assertGreater(brackets["take_profit_price"], 151.50)
        self.assertLess(brackets["take_profit_price"], 154.00)

        sizing = self.trigger.calculate_position_sizing(
            portfolio_value=5000.0,
            entry_price=150.00,
            stop_loss_price=brackets["stop_loss_price"],
            take_profit_price=brackets["take_profit_price"],
            strategy_params=self.params,
        )
        self.assertTrue(sizing["viable"], msg=sizing.get("rejection_reason"))
        # Riesgo total máximo ~$14 (0.28% de 5000), jamás -$40 o -$50
        self.assertLessEqual(sizing["max_risk_eur"], 15.0)
        self.assertGreaterEqual(sizing["expected_profit_eur"], 8.0)

    def test_2_commission_and_min_net_profit_gate(self):
        # Si el potencial neto es < $8.00 (1 acción * $8.50 bruto - $2 comm = $6.50 netos), debe rechazar
        bad_sizing = self.trigger.calculate_position_sizing(
            portfolio_value=5000.0,
            entry_price=200.00,
            stop_loss_price=195.00,
            take_profit_price=208.50,  # 1.70R >= 1.60R, pero con riesgo 0.20% ($10 -> $8/5 = 1 acc) solo deja $6.50 neto
            strategy_params={**self.params, "max_portfolio_risk_per_trade_pct": 0.0020},
        )
        self.assertFalse(bad_sizing["viable"])
        self.assertIn("neto", bad_sizing["rejection_reason"].lower())

        # Techo estructural demasiado cercano (< 1.60R) debe rechazarse
        low_rr = self.trigger.calculate_position_sizing(
            portfolio_value=5000.0,
            entry_price=100.00,
            stop_loss_price=98.50,
            take_profit_price=101.80,  # 1.20R < 1.60R
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

    def test_4_three_tricks_intraday_vetoes_and_ignition(self):
        df_d = _make_daily_df(bullish=True)
        daily = evaluate_daily_structure(df_d)
        self.assertTrue(daily["daily_trend_ok"])

        # Caso A: Setup limpio Los 3 Trucos -> DISPARA
        df_15m_ok = _make_intraday_15m_pullback_ignition()
        intra_ok = extract_intraday_features(df_15m_ok)
        self.assertIsNotNone(intra_ok)
        res_ok = evaluate_three_tricks(daily, intra_ok, self.params)
        self.assertTrue(res_ok["signal_triggered"], msg=res_ok.get("block_reason"))
        self.assertEqual(res_ok["setup_archetype"], "THREE_TRICKS_INTRADAY_PULLBACK")
        self.assertGreaterEqual(res_ok["quant_score"], 70)

        # Caso B: Sobreextendido / 3 velas verdes verticales -> BLOQUEA
        df_15m_ext = _make_intraday_15m_pullback_ignition(overextended=True)
        intra_ext = extract_intraday_features(df_15m_ext)
        res_ext = evaluate_three_tricks(daily, intra_ext, self.params)
        self.assertFalse(res_ext["signal_triggered"])

        # Caso C: Comité #2 divergencia volumen (pullback rojo > impulso verde) -> BLOQUEA
        df_15m_div = _make_intraday_15m_pullback_ignition(red_volume_heavy=True)
        intra_div = extract_intraday_features(df_15m_div)
        res_div = evaluate_three_tricks(daily, intra_div, self.params)
        self.assertFalse(res_div["signal_triggered"])
        self.assertIn("Comité [Volumen]", res_div["block_reason"])

        # Caso D: Comité #1 RSI > 70 -> BLOQUEA
        df_15m_rsi = _make_intraday_15m_pullback_ignition(high_rsi=True)
        intra_rsi = extract_intraday_features(df_15m_rsi)
        res_rsi = evaluate_three_tricks(daily, intra_rsi, self.params)
        self.assertFalse(res_rsi["signal_triggered"])

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

    def test_6_position_guardian_protections_and_eod_swing(self):
        midday_utc = datetime(2026, 4, 9, 17, 0, tzinfo=timezone.utc)  # 13:00 ET
        eod_utc = datetime(2026, 4, 9, 19, 50, tzinfo=timezone.utc)    # 15:50 ET (ventana EOD 15m)

        # A) Revisor de Entradas (Early Abort): cae bajo VWAP con vela roja y RVOL >= 1.30x
        abort_res = evaluate_position_guardian(
            symbol="MSFT",
            qty=10,
            entry_price=150.0,
            current_price=149.40,
            current_sl=148.80,
            current_tp=152.30,
            peak_price=150.10,
            initial_risk_per_share=1.20,
            unrealized_pl=-6.0,
            clv_daily=0.35,
            intraday_sig={
                "has_intraday": True,
                "vwap": 149.90,
                "below_vwap": True,
                "rvol_intraday": 1.45,
                "last_open": 149.85,
                "last_close": 149.40,
            },
            params=self.params,
            now_utc=midday_utc,
        )
        self.assertEqual(abort_res["action_type"], "CLOSE_IMMEDIATELY")
        self.assertEqual(abort_res["regime_code"], "EARLY_ABORT_BY_REVIEWER")

        # B) Breakeven+ a +$8.00: asegura comisiones ($2) + colchón ($2) = +$4/10 = $150.40
        be_res = evaluate_position_guardian(
            symbol="NVDA",
            qty=10,
            entry_price=150.0,
            current_price=150.85,
            current_sl=148.80,
            current_tp=152.30,
            peak_price=150.90,
            initial_risk_per_share=1.20,
            unrealized_pl=8.50,
            clv_daily=0.75,
            intraday_sig={"vwap": 150.10, "below_vwap": False, "below_ema9": False, "selling_rvol": 0.9},
            params=self.params,
            now_utc=midday_utc,
        )
        self.assertEqual(be_res["action_type"], "ADJUST_SL")
        self.assertEqual(be_res["regime_code"], "BREAKEVEN_LOCK")
        self.assertGreaterEqual(be_res["recommended_sl"], 150.40)

        # C) Trailing Ratchet Etapa 1 (pico +$15 >= +$12) bloquea 60% de MFE ($150.0 + 0.60*1.5 = $150.90)
        tr1_res = evaluate_position_guardian(
            symbol="NVDA",
            qty=10,
            entry_price=150.0,
            current_price=151.30,
            current_sl=150.40,
            current_tp=152.40,
            peak_price=151.50,
            initial_risk_per_share=1.20,
            unrealized_pl=13.00,
            clv_daily=0.80,
            intraday_sig={"vwap": 150.20, "below_vwap": False, "below_ema9": False, "selling_rvol": 0.8},
            params=self.params,
            now_utc=midday_utc,
        )
        self.assertEqual(tr1_res["action_type"], "ADJUST_SL")
        self.assertEqual(tr1_res["regime_code"], "TRAILING_RATCHET_60")
        self.assertEqual(tr1_res["recommended_sl"], 150.90)

        # D) Si retrocede tras pico +$15 por debajo del suelo garantizado ($150.90), CIERRA INMEDIATAMENTE
        lock_close = evaluate_position_guardian(
            symbol="NVDA",
            qty=10,
            entry_price=150.0,
            current_price=150.85,
            current_sl=150.40,
            current_tp=152.40,
            peak_price=151.50,
            initial_risk_per_share=1.20,
            unrealized_pl=8.50,
            clv_daily=0.50,
            intraday_sig={"vwap": 150.90, "below_vwap": True, "below_ema9": True, "selling_rvol": 1.0},
            params=self.params,
            now_utc=midday_utc,
        )
        self.assertEqual(lock_close["action_type"], "CLOSE_IMMEDIATELY")
        self.assertEqual(lock_close["regime_code"], "PROFIT_LOCK_MARKET_CLOSE")

        # E) Ventana EOD NY (15:50 ET): sin colchón >= 1.5R -> cierra EOD
        eod_close = evaluate_position_guardian(
            symbol="AAPL",
            qty=10,
            entry_price=150.0,
            current_price=150.40,
            current_sl=148.80,
            current_tp=152.30,
            peak_price=150.50,
            initial_risk_per_share=1.20,
            unrealized_pl=4.0,
            clv_daily=0.55,
            intraday_sig={"vwap": 150.10, "below_vwap": False, "below_ema9": False, "selling_rvol": 0.9},
            params=self.params,
            now_utc=eod_utc,
        )
        self.assertEqual(eod_close["action_type"], "CLOSE_IMMEDIATELY")
        self.assertEqual(eod_close["regime_code"], "EOD_CLOSE_POSITION")

        # F) Ventana EOD NY (15:50 ET): >= +1.5R, CLV_1D >= 0.70, SL ya en BE+/Ratchet -> prórroga Swing aprobada (no cierra EOD)
        eod_swing = evaluate_position_guardian(
            symbol="AAPL",
            qty=10,
            entry_price=150.0,
            current_price=151.90,
            current_sl=151.50,
            current_tp=152.40,
            peak_price=152.00,
            initial_risk_per_share=1.20,
            unrealized_pl=19.0,
            clv_daily=0.82,
            intraday_sig={"vwap": 150.50, "below_vwap": False, "below_ema9": False, "rvol_intraday": 0.8},
            params=self.params,
            now_utc=eod_utc,
        )
        self.assertEqual(eod_swing["action_type"], "NONE")
        self.assertNotEqual(eod_swing["regime_code"], "EOD_CLOSE_POSITION")

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
        self.assertEqual(data["version"], 50)
        self.assertEqual(data["commission_per_order"], 1.0)
        self.assertEqual(data["max_comm_ratio"], 0.25)
        self.assertEqual(data["min_net_profit_usd"], 8.0)
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
