"""Signal scoring: TP/SL/timeout resolution correctness (the core surface)."""
import numpy as np
import pandas as pd

from fabletradebot.v3 import V3Config, v3_config
from fabletradebot.scoring import (open_position, step_position, simulate_scoring,
                                   summarize, WIN, LOSS, TIMEOUT_WIN, TIMEOUT_LOSS, OPEN)


def _pos(direction=1, entry=100.0, tp=None, sl=None):
    cfg = V3Config()
    p = open_position("v4", "BTC", direction, entry, sigma_day=0.02,
                      ts=pd.Timestamp("2026-01-01", tz="UTC"), cfg=cfg,
                      weight=0.3, equity=100_000.0)
    if tp is not None:
        p["tp"] = tp
    if sl is not None:
        p["sl"] = sl
        p["risk"] = abs(entry - sl)
    return p


def test_open_position_places_tp_sl_by_sigma():
    p = _pos(direction=1, entry=100.0)
    # tp_k=2.0, sl_k=1.5, sigma_day=0.02 -> +4% / -3%
    assert abs(p["tp"] - 104.0) < 1e-9
    assert abs(p["sl"] - 97.0) < 1e-9
    assert p["status"] == OPEN and p["id"].startswith("v4:BTC:")


def test_long_take_profit():
    p = _pos(1, 100.0, tp=104.0, sl=97.0)
    assert step_position(p, high=104.5, low=101.0, close=104.0, ts=_ts(1))
    assert p["status"] == WIN and abs(p["result_r"] - (4.0 / 3.0)) < 1e-9


def test_long_stop_loss():
    p = _pos(1, 100.0, tp=104.0, sl=97.0)
    assert step_position(p, high=101.0, low=96.5, close=98.0, ts=_ts(1))
    assert p["status"] == LOSS and abs(p["result_r"] + 1.0) < 1e-9


def test_same_bar_tie_is_conservative_loss():
    p = _pos(1, 100.0, tp=104.0, sl=97.0)
    assert step_position(p, high=105.0, low=96.0, close=100.0, ts=_ts(1))
    assert p["status"] == LOSS


def test_short_take_profit_and_stop():
    p = _pos(-1, 100.0, tp=96.0, sl=103.0)      # short: profit below, stop above
    assert step_position(p, high=101.0, low=95.5, close=96.0, ts=_ts(1))
    assert p["status"] == WIN and p["result_r"] > 0
    p2 = _pos(-1, 100.0, tp=96.0, sl=103.0)
    assert step_position(p2, high=103.5, low=99.0, close=102.0, ts=_ts(1))
    assert p2["status"] == LOSS


def test_timeout_classification():
    p = _pos(1, 100.0, tp=110.0, sl=90.0)       # wide -> neither hit
    late = pd.Timestamp("2026-01-20", tz="UTC")  # past 7d timeout
    assert step_position(p, high=103.0, low=99.0, close=102.0, ts=late)
    assert p["status"] == TIMEOUT_WIN and p["result_r"] > 0
    p2 = _pos(1, 100.0, tp=110.0, sl=90.0)
    assert step_position(p2, high=101.0, low=97.0, close=98.0, ts=late)
    assert p2["status"] == TIMEOUT_LOSS and p2["result_r"] < 0


def test_stays_open_before_timeout_without_touch():
    p = _pos(1, 100.0, tp=110.0, sl=90.0)
    assert step_position(p, high=101.0, low=99.0, close=100.5, ts=_ts(1)) is False
    assert p["status"] == OPEN


def _ts(day):
    return pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=day)


def _synth_panel(n=200):
    idx = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    data, sigs = {}, {}
    for a, drift in (("BTC", 0.004), ("ETH", -0.004), ("SOL", 0.0), ("HYPE", 0.0)):
        c = 100.0 * np.exp(np.cumsum(np.full(n, drift)))
        data[a] = pd.DataFrame({"high": c * 1.002, "low": c * 0.998, "close": c},
                               index=idx)
        sigs[a] = pd.DataFrame({"vol_ann": np.full(n, 0.5)}, index=idx)
    return idx, data, sigs


def test_simulate_scoring_opens_and_resolves():
    idx, data, sigs = _synth_panel()
    # BTC persistently long, ETH persistently short
    weights = pd.DataFrame({"BTC": 0.3, "ETH": -0.3, "SOL": 0.0, "HYPE": 0.0},
                           index=idx)
    equity = pd.Series(100_000.0, index=idx)
    positions = simulate_scoring(weights, data, sigs, equity, v3_config(), "v3")
    assert positions
    btc = [p for p in positions if p["asset"] == "BTC"]
    assert btc and btc[0]["direction"] == 1
    # a steady up-drift BTC long should hit TP -> Win, not stop out
    resolved = [p for p in positions if p["status"] != OPEN]
    assert any(p["asset"] == "BTC" and p["status"] == WIN for p in resolved)
    s = summarize(positions)
    assert s["n"] == len(resolved) and 0.0 <= s["win_rate"] <= 1.0


def test_ids_are_deterministic():
    idx, data, sigs = _synth_panel()
    weights = pd.DataFrame({"BTC": 0.3, "ETH": -0.3, "SOL": 0.0, "HYPE": 0.0},
                           index=idx)
    eq = pd.Series(100_000.0, index=idx)
    a = simulate_scoring(weights, data, sigs, eq, v3_config(), "v3")
    b = simulate_scoring(weights, data, sigs, eq, v3_config(), "v3")
    assert [p["id"] for p in a] == [p["id"] for p in b]
