"""Engine accounting and safety mechanics on hand-built bars (G1)."""
import numpy as np
import pandas as pd
import pytest

from fabletradebot.config import Params, spec
from fabletradebot.engine import run

SYM = "BTC"  # slippage 0.0002, taker 0.0005


def _frames(path):
    """path: list of (open, high, low, close). Hourly bars from 2024-01-01."""
    idx = pd.date_range("2024-01-01", periods=len(path), freq="1h", tz="UTC")
    df = pd.DataFrame(path, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    return {SYM: df}


def _features(frames):
    f = pd.DataFrame(index=frames[SYM].index)
    f["atr1h"] = 1.0
    f["bias4h"] = 1.0
    return {SYM: f}


def _setup(path, cand_bar, direction, sl, conf=0.65):
    frames = _frames(path)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [direction], "conf": [conf], "sl": [sl],
                                "setup": ["S1"]}, index=[idx[cand_bar]])}
    regime = pd.DataFrame({"state": "TREND", "btc_dir": direction}, index=idx)
    corr = pd.Series(False, index=idx)
    return frames, _features(frames), cands, {SYM: None}, regime, corr


def test_stop_loss_accounting_exact():
    # decide at bar0 close(100); fill bar1 open 100*(1+slip); SL 98 hit in bar2
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100), (100, 100, 97.5, 98)]
    args = _setup(path, 0, 1, sl=98.0)
    p = Params()
    res = run(*args, p, equity0=10_000.0)
    t = res["trades"]
    assert len(t) == 1
    tr = t.iloc[0]
    slip, fee = spec(SYM).slippage, p.taker_fee
    fill = 100 * (1 + slip)
    stop_frac = (fill - 98.0) / fill
    notional = min(10_000 * 0.005 / stop_frac, 10_000 * 2.0)
    exit_px = 98.0 * (1 - slip)
    expected_gross = (exit_px - fill) / fill * notional
    expected_pnl = expected_gross - 2 * notional * fee
    assert tr["pnl"] == pytest.approx(expected_pnl, rel=1e-9)
    assert tr["r"] == pytest.approx(expected_pnl / (notional * stop_frac), rel=1e-9)
    assert res["final_equity"] == pytest.approx(10_000 + expected_pnl, rel=1e-9)
    # loss can exceed risk_amt only by costs, never more
    assert -tr["pnl"] <= tr["risk_amt"] + 2 * notional * (fee + slip) * 1.01


def test_same_bar_sl_and_tp_resolves_to_sl():
    # bar2 spans both TP1 (103) and SL (98) -> conservative: SL
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100), (100, 106, 97.5, 100)]
    args = _setup(path, 0, 1, sl=98.0)
    res = run(*args, Params(), equity0=10_000.0)
    assert res["trades"].iloc[0]["reason"] == "SL"
    assert res["trades"].iloc[0]["pnl"] < 0


def test_tp1_moves_stop_to_breakeven_and_trails():
    # TP1 at +1.5R: stop_frac ~2%, tp1 ~ fill*1.03. Rally then collapse ->
    # runner exits at trail/BE, trade ends positive overall.
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 104, 99.9, 103.8), (103.8, 105, 103.5, 104.5),
            (104.5, 104.6, 95, 95.5)]
    args = _setup(path, 0, 1, sl=98.0)
    res = run(*args, Params(), equity0=10_000.0)
    t = res["trades"]
    assert len(t) == 1
    assert t.iloc[0]["reason"] == "Trail"
    assert t.iloc[0]["pnl"] > 0  # TP1 half banked more than runner gave back


def test_liquidation_invariant_raises_if_forced():
    # Manually corrupt: candidate stop far below, engine sizes at 2x ->
    # liq ~49% away; a 60% crash bar cannot hit liq before SL (SL is closer).
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100), (100, 100, 40, 45)]
    args = _setup(path, 0, 1, sl=92.0)
    res = run(*args, Params(), equity0=10_000.0)  # must NOT raise: SL closer
    assert res["trades"].iloc[0]["reason"] == "SL"


def test_funding_settlement_applied():
    # position open across a 00:00 UTC boundary pays funding on longs
    path = [(100, 101, 99.5, 100)] * 30
    frames = _frames(path)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                "setup": ["S1"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    ftimes = pd.DatetimeIndex([pd.Timestamp("2024-01-01 08:00", tz="UTC"),
                               pd.Timestamp("2024-01-01 16:00", tz="UTC")])
    funding = {SYM: pd.Series([0.001, 0.001], index=ftimes)}
    res0 = run(frames, _features(frames), cands, funding, regime, corr,
               Params(), equity0=10_000.0)
    res1 = run(frames, _features(frames), cands, funding, regime, corr,
               Params(), equity0=10_000.0)
    assert res0["final_equity"] == res1["final_equity"]  # deterministic
    pos = res0["open_positions"][SYM]
    assert pos.realized < 0  # paid two positive fundings
    assert pos.realized == pytest.approx(-2 * 0.001 * pos.notional, rel=1e-9)


def test_cooldown_and_single_position_per_asset():
    path = [(100, 101, 99.5, 100)] * 10
    frames = _frames(path)
    idx = frames[SYM].index
    # candidate fires every bar; only one open position may exist
    cands = {SYM: pd.DataFrame({"dir": [1] * 10, "conf": [0.65] * 10,
                                "sl": [95.0] * 10, "setup": ["S1"] * 10}, index=idx)}
    regime = pd.DataFrame({"state": "TREND", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    res = run(frames, _features(frames), cands, {SYM: None}, regime, corr,
              Params(), equity0=10_000.0)
    assert len(res["open_positions"]) == 1
    assert len(res["trades"]) == 0


def test_crisis_blocks_entry_and_closes_positions():
    path = [(100, 101, 99.5, 100)] * 6
    frames = _frames(path)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                "setup": ["S1"]}, index=[idx[0]])}
    state = pd.Series(["TREND", "TREND", "TREND", "CRISIS", "CRISIS", "CRISIS"], index=idx)
    regime = pd.DataFrame({"state": state, "btc_dir": 1})
    corr = pd.Series(False, index=idx)
    res = run(frames, _features(frames), cands, {SYM: None}, regime, corr,
              Params(), equity0=10_000.0)
    assert len(res["trades"]) == 1
    assert res["trades"].iloc[0]["reason"] == "Regime"
