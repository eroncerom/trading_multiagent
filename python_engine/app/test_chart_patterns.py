"""
Tests unitarios para el Motor de Figuras Chartistas (chart_patterns_engine.py)
y el Agente Técnico Defensivo + Puerta Dual fail-closed (technical_agent.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from chart_patterns_engine import (
    analyze_chart_patterns,
    confirm_hourly_entry,
    detect_consolidation_breakout,
    detect_double_or_triple_bottom,
    detect_inverse_head_and_shoulders,
)
from technical_agent import evaluate_dual_gate, evaluate_technical_gate


def _make_ohlcv(closes: list[float], volumes: list[float] | None = None, freq: str = "1D") -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq=freq)
    c = np.array(closes, dtype=float)
    o = np.roll(c, 1)
    o[0] = c[0]
    h = np.maximum(o, c) * 1.005
    l = np.minimum(o, c) * 0.995
    v = np.array(volumes if volumes is not None else [1_000_000.0] * n, dtype=float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


def _interpolate_waypoints(waypoints: list[tuple[int, float]]) -> list[float]:
    total_len = waypoints[-1][0] + 1
    out = np.zeros(total_len, dtype=float)
    for (i0, p0), (i1, p1) in zip(waypoints[:-1], waypoints[1:]):
        seg = np.linspace(p0, p1, i1 - i0 + 1)
        out[i0 : i1 + 1] = seg
    return out.tolist()


def test_inverse_head_and_shoulders_confirmed():
    # Bajada previa -> Hombro Izq (95) -> Pico1 (105) -> Cabeza (86) -> Pico2 (106) -> Hombro Der (96) -> Ruptura (108.5)
    closes = _interpolate_waypoints([
        (0, 118.0),
        (10, 108.0),
        (18, 95.0),   # Left shoulder
        (26, 105.0),  # Neckline P1
        (36, 86.0),   # Head (>9% debajo de hombros)
        (46, 106.0),  # Neckline P2
        (54, 96.0),   # Right shoulder (~1.05% diff vs LS)
        (60, 105.5),
        (61, 109.0),  # Breakout confirmado > +1% sobre neckline
    ])
    vols = [1_000_000.0] * len(closes)
    vols[-1] = 2_000_000.0  # RVOL = 2.0x
    df = _make_ohlcv(closes, vols)

    res = detect_inverse_head_and_shoulders(df)
    assert res is not None, "Debería detectar HCH invertido"
    assert res["pattern"] == "INVERSE_HEAD_AND_SHOULDERS"
    assert res["status"] == "CONFIRMED"
    assert res["projected_target"] > res["neckline"]
    assert res["invalidation_price"] < res["pivots"]["right_shoulder"]
    assert res["breakout_rvol"] >= 1.3


def test_double_bottom_confirmed_and_fakeout_rejected_without_volume():
    # Doble suelo en ~90 con neckline en ~102 y ruptura final en 104.5
    closes = _interpolate_waypoints([
        (0, 112.0),
        (15, 90.0),   # Bottom 1
        (28, 102.0),  # Neckline peak (+13.3%)
        (42, 91.0),   # Bottom 2 (1.1% diff <= 2.5%)
        (51, 101.5),
        (52, 104.5),  # Breakout > 102 * 1.01
    ])
    vols_strong = [1_000_000.0] * len(closes)
    vols_strong[-1] = 1_800_000.0
    df_strong = _make_ohlcv(closes, vols_strong)

    res_strong = detect_double_or_triple_bottom(df_strong)
    assert res_strong is not None, "Debería detectar Doble Suelo"
    assert res_strong["pattern"] == "DOUBLE_BOTTOM"
    assert res_strong["status"] == "CONFIRMED"
    assert res_strong["projected_target"] > 112.0

    # Sin volumen suficiente -> debe bajar a FORMING (defensivo)
    vols_weak = [1_000_000.0] * len(closes)
    vols_weak[-1] = 900_000.0
    df_weak = _make_ohlcv(closes, vols_weak)
    res_weak = detect_double_or_triple_bottom(df_weak)
    assert res_weak is not None
    assert res_weak["status"] == "FORMING", "Sin volumen >= 1.3x no debe confirmar ruptura"


def test_consolidation_breakout_confirmed():
    # Caja estrecha de 25 días entre 100 y 106 (6% altura) y ruptura final a 108.5 con volumen
    rng = np.random.default_rng(42)
    base = list(103.0 + np.sin(np.linspace(0, 6 * np.pi, 35)) * 2.5 + rng.normal(0, 0.1, 35))
    closes = base + [105.0, 108.5]
    vols = [1_000_000.0] * len(closes)
    vols[-1] = 1_900_000.0
    df = _make_ohlcv(closes, vols)

    res = detect_consolidation_breakout(df)
    assert res is not None, "Debería detectar ruptura de rango"
    assert res["pattern"] == "CONSOLIDATION_BREAKOUT"
    assert res["status"] == "CONFIRMED"
    assert res["projected_target"] > res["neckline"]


def test_hourly_confirmation_pass_and_fail():
    # Tendencia horaria alcista limpia cerrando sobre EMA20 y neckline 105
    closes_ok = list(np.linspace(101.0, 107.0, 30))
    df_1h_ok = _make_ohlcv(closes_ok, freq="1h")
    conf_ok = confirm_hourly_entry(df_1h_ok, neckline=105.0)
    assert conf_ok["available"] is True
    assert conf_ok["confirmed"] is True

    # Cierre horario por debajo de neckline
    closes_bad = list(np.linspace(106.0, 102.0, 30))
    df_1h_bad = _make_ohlcv(closes_bad, freq="1h")
    conf_bad = confirm_hourly_entry(df_1h_bad, neckline=105.0)
    assert conf_bad["available"] is True
    assert conf_bad["confirmed"] is False


def test_technical_agent_and_fail_closed_dual_gate():
    closes = _interpolate_waypoints([
        (0, 112.0),
        (15, 90.0),
        (28, 102.0),
        (42, 91.0),
        (51, 101.5),
        (52, 104.5),
    ])
    vols = [1_000_000.0] * len(closes)
    vols[-1] = 1_800_000.0
    df_daily = _make_ohlcv(closes, vols)
    df_1h_ok = _make_ohlcv(list(np.linspace(100.0, 105.0, 30)), freq="1h")

    daily_struct = {
        "price": 104.5,
        "ema_20": 98.0,
        "sma_50": 97.0,
        "sma_200": 95.0,
        "plus_di": 28.0,
        "minus_di": 16.0,
        "rsi_14": 58.0,
        "ret_1d_pct": 2.95,
        "falling_knife": False,
        "daily_trend_ok": True,
    }

    tech_ok = evaluate_technical_gate(daily_struct, df_daily, df_1h=df_1h_ok)
    assert tech_ok["verdict"] == "APPROVE"
    assert tech_ok["pattern_status"] == "CONFIRMED"

    # Falling knife -> REJECT inmediato
    knife_struct = {**daily_struct, "falling_knife": True, "ret_1d_pct": -5.2}
    tech_knife = evaluate_technical_gate(knife_struct, df_daily, df_1h=df_1h_ok)
    assert tech_knife["verdict"] == "REJECT"

    # Puerta Dual fail-closed: solo APPROVE + APPROVE pasa
    ai_approve = {"status": "DONE", "verdict": "APPROVE", "headline": "Sin catalizadores tóxicos."}
    ai_caution = {"status": "DONE", "verdict": "CAUTION", "headline": "Earnings en 48h."}
    ai_unavailable = {"status": "UNAVAILABLE", "verdict": None, "reason": "Timeout"}

    g_pass = evaluate_dual_gate(ai_approve, tech_ok, {"dual_gate_fail_closed": True})
    assert g_pass["passed"] is True
    assert g_pass["status"] == "APPROVED"

    g_caution = evaluate_dual_gate(ai_caution, tech_ok, {"dual_gate_fail_closed": True})
    assert g_caution["passed"] is False
    assert g_caution["status"] == "BLOCKED_FUNDAMENTAL"

    g_unavail = evaluate_dual_gate(ai_unavailable, tech_ok, {"dual_gate_fail_closed": True})
    assert g_unavail["passed"] is False
    assert g_unavail["status"] == "BLOCKED_FUNDAMENTAL"

    g_tech_rej = evaluate_dual_gate(ai_approve, tech_knife, {"dual_gate_fail_closed": True})
    assert g_tech_rej["passed"] is False
    assert g_tech_rej["status"] == "BLOCKED_TECHNICAL"


if __name__ == "__main__":
    test_inverse_head_and_shoulders_confirmed()
    test_double_bottom_confirmed_and_fakeout_rejected_without_volume()
    test_consolidation_breakout_confirmed()
    test_hourly_confirmation_pass_and_fail()
    test_technical_agent_and_fail_closed_dual_gate()
    print("ALL CHART PATTERN & DUAL GATE TESTS PASSED!")
