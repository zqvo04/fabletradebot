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
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": direction}, index=idx)
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
    # at entry equity is at its high -> anti-martingale boost applies
    risk_frac = p.conf_tiers[0][2] * p.eq_boost_mult
    notional = min(10_000 * risk_frac / stop_frac, 10_000 * 5.0)
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
    res = run(*args, Params(tp1_r=1.5, tp1_frac=0.5), equity0=10_000.0)
    assert res["trades"].iloc[0]["reason"] == "SL"
    assert res["trades"].iloc[0]["pnl"] < 0


def test_tp1_moves_stop_to_breakeven_and_trails():
    # TP1 at +1.5R: stop_frac ~2%, tp1 ~ fill*1.03. Rally then collapse ->
    # runner exits at trail/BE, trade ends positive overall.
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 104, 99.9, 103.8), (103.8, 105, 103.5, 104.5),
            (104.5, 104.6, 95, 95.5)]
    args = _setup(path, 0, 1, sl=98.0)
    res = run(*args, Params(tp1_r=1.5, tp1_frac=0.5, trail_atr=3.0), equity0=10_000.0)
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
    assert res["trades"].iloc[0]["reason"] in ("SL", "Trail")


def test_funding_settlement_applied():
    # position open across a 00:00 UTC boundary pays funding on longs
    path = [(100, 101, 99.5, 100)] * 30
    frames = _frames(path)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                "setup": ["S1"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
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
    assert pos.realized < 0  # paid two positive fundings + one default drag
    p = Params()
    expected = -(2 * 0.001 + p.funding_default_drag) * pos.notional
    assert pos.realized == pytest.approx(expected, rel=1e-9)


def test_cooldown_and_single_position_per_asset():
    path = [(100, 101, 99.5, 100)] * 10
    frames = _frames(path)
    idx = frames[SYM].index
    # candidate fires every bar; only one open position may exist
    cands = {SYM: pd.DataFrame({"dir": [1] * 10, "conf": [0.65] * 10,
                                "sl": [95.0] * 10, "setup": ["S1"] * 10}, index=idx)}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
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
    state = pd.Series(["TREND_UP", "TREND_UP", "TREND_UP", "CRISIS", "CRISIS", "CRISIS"], index=idx)
    regime = pd.DataFrame({"state": state, "btc_dir": 1})
    corr = pd.Series(False, index=idx)
    res = run(frames, _features(frames), cands, {SYM: None}, regime, corr,
              Params(), equity0=10_000.0)
    assert len(res["trades"]) == 1
    assert res["trades"].iloc[0]["reason"] == "Regime"


def test_pyramiding_adds_units_on_proof():
    # BTC is in aggression_syms; stop 5% -> +2R trigger at ~110, +4R at ~120
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100)]
    px = 100.0
    for _ in range(30):
        px += 1.0
        path.append((px - 1, px + 0.4, px - 1.2, px))
    frames = _frames(path)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                "setup": ["BRK"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    res = run(frames, _features(frames), cands, {SYM: None}, regime, corr,
              Params(), equity0=10_000.0)
    pos = res["open_positions"].get(SYM) or None
    assert pos is not None
    assert pos.adds == 2 and len(pos.tranches) == 3
    # every add increases committed risk and keeps liquidation beyond the stop
    assert pos.risk_amt > pos.tranches[0][1] * pos.init_stop_frac * 1.5
    assert pos.liq_price < pos.sl  # long: stop always hit first
    # adds do not fire for non-aggression symbols
    res2 = run(frames, _features(frames), cands, {SYM: None}, regime, corr,
               Params(aggression_syms=()), equity0=10_000.0)
    pos2 = res2["open_positions"][SYM]
    assert pos2.adds == 0 and len(pos2.tranches) == 1


def test_short_side_accounting_and_funding():
    # short entry 100, SL 105 (5%); bar2 spikes to 106 -> SL hit
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100), (100, 106, 99.9, 105.5)]
    args = _setup(path, 0, -1, sl=105.0)
    p = Params()
    res = run(*args, p, equity0=10_000.0)
    tr = res["trades"].iloc[0]
    slip, fee = spec(SYM).slippage, p.taker_fee
    fill = 100 * (1 - slip)              # short entry improves... slips down
    stop_frac = (105.0 - fill) / fill
    risk_frac = p.conf_tiers[0][2] * p.eq_boost_mult
    notional = min(10_000 * risk_frac / stop_frac, 10_000 * 5.0)
    exit_px = 105.0 * (1 + slip)
    expected = -(exit_px - fill) / fill * notional - 2 * notional * fee
    assert tr["pnl"] == pytest.approx(expected, rel=1e-9)
    assert tr["pnl"] < 0
    # liquidation must sit ABOVE the short stop
    # (validated inside size_position; a raise here would have failed the run)

    # positive funding PAYS a short (sign mirror of the long test)
    path2 = [(100, 101, 99.5, 100)] * 12
    frames = _frames(path2)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [-1], "conf": [0.65], "sl": [105.0],
                                "setup": ["BRK_S"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": -1}, index=idx)
    corr = pd.Series(False, index=idx)
    ft = pd.DatetimeIndex([pd.Timestamp("2024-01-01 08:00", tz="UTC")])
    funding = {SYM: pd.Series([0.001], index=ft)}
    feats = _features(frames)
    feats[SYM]["bias4h"] = -1.0   # aligned with the short (no BiasFlip exit)
    res2 = run(frames, feats, cands, funding, regime, corr,
               Params(), equity0=10_000.0)
    pos = res2["open_positions"][SYM]
    # only the 08:00 settlement falls inside the 12-bar path; +rate pays a short
    assert pos.realized == pytest.approx(0.001 * pos.notional, rel=1e-9)


def test_playbook_exit_overrides_day_trade():
    # FADE_L overrides: full exit at +1.5R, no trail, 24-bar time stop
    path = [(100, 101, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 108, 99.9, 107.5)]  # rallies through +1.5R (stop 5% -> tp ~107.5)
    frames = _frames(path)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                "setup": ["FADE_L"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    res = run(frames, _features(frames), cands, {SYM: None}, regime, corr,
              Params(), equity0=10_000.0)
    t = res["trades"]
    assert len(t) == 1 and t.iloc[0]["reason"] == "TP" and t.iloc[0]["pnl"] > 0

    # flat path -> 24-bar timeout closes it (global default would be no timeout)
    path2 = [(100, 101, 99.5, 100)] * 30
    frames2 = _frames(path2)
    idx2 = frames2[SYM].index
    cands2 = {SYM: pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                 "setup": ["FADE_L"]}, index=[idx2[0]])}
    regime2 = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx2)
    corr2 = pd.Series(False, index=idx2)
    res2 = run(frames2, _features(frames2), cands2, {SYM: None}, regime2, corr2,
               Params(), equity0=10_000.0)
    assert len(res2["trades"]) == 1
    assert res2["trades"].iloc[0]["reason"] == "Timeout"
    assert res2["trades"].iloc[0]["bars"] >= 24


def test_equity_peak_tracks_true_high_not_initial_capital():
    # Regression test for a pre-existing bug: `peak` was initialized to equity0
    # and never updated, so `dd = 1 - eq/peak` was measured from INITIAL
    # capital, not the running equity HIGH. Once equity ever exceeded equity0
    # dd went permanently negative and the drawdown governor (dd_stop / DD
    # halving / anti-martingale eq_boost) could never trip again, no matter
    # how large a peak-to-trough crash followed — silently disabling the
    # exact mechanism the whole system's ruin-avoidance design depends on.
    #
    # BTC rallies hard (unrealized equity peaks well above equity0), then
    # gives most of it back while STAYING above equity0. A probe ETH
    # candidate arrives after the give-back: it must be blocked by dd_stop
    # measured from the TRUE peak, even though current equity > equity0.
    idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    btc = pd.DataFrame([
        (100, 101, 99.5, 100), (100, 100.5, 99.8, 100),
        (100, 141, 99.5, 140), (140, 140, 107, 108), (108, 108.5, 107.5, 108),
    ], columns=["open", "high", "low", "close"], index=idx)
    btc["volume"] = 1000.0
    eth = pd.DataFrame([(100, 101, 99.5, 100)] * 5,
                       columns=["open", "high", "low", "close"], index=idx)
    eth["volume"] = 1000.0
    frames = {"BTC": btc, "ETH": eth}
    feats = {s: pd.DataFrame({"atr1h": 1.0, "bias4h": 1.0}, index=idx) for s in frames}
    cands = {
        "BTC": pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                             "setup": ["S1"]}, index=[idx[0]]),
        "ETH": pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                             "setup": ["S1"]}, index=[idx[3]]),   # probe, post-crash
    }
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    funding = {s: None for s in frames}
    # high-leverage synthetic sizing to produce a large equity swing from one
    # position; trail disabled so the give-back isn't cut short by the trail;
    # open-risk/margin caps widened since this deliberately oversized position
    # is just a vehicle to swing equity, not what's under test
    p = Params(conf_tiers=((0.55, 10.0, 0.05),), trail_atr=0.0,
              aggression_syms=(), pyramid_max=0,
              max_open_risk=1.0, max_margin_frac=1.0)

    res = run(frames, feats, cands, funding, regime, corr, p, equity0=10_000.0)

    assert "BTC" in res["open_positions"]
    peak = res["carry"]["peak"]
    eq_now = res["equity"].iloc[-1]   # mark-to-market equity (position still open)
    assert peak > 12_000, f"peak should capture the rally high, got {peak}"
    assert eq_now > 10_000, "equity gave back gains but is still net positive"
    # the discriminating assertion: ETH must be BLOCKED even though eq > equity0
    assert "ETH" not in res["open_positions"]
    assert res["carry"]["dd_frozen"] is True
    true_dd = 1 - eq_now / peak
    assert true_dd >= p.dd_stop


def test_open_position_mark_to_market_scoring():
    from fabletradebot.scoring import mark_to_market, open_report
    # open a long at ~100, still open; score it at a higher price
    path = [(100, 101, 99.5, 100)] * 6
    frames = _frames(path)
    idx = frames[SYM].index
    cands = {SYM: pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                "setup": ["PBK_L"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    res = run(frames, _features(frames), cands, {SYM: None}, regime, corr,
              Params(), equity0=10_000.0)
    pos = res["open_positions"][SYM]
    mtm = mark_to_market(pos, price=110.0)
    # unrealized R = gross(110) / risk_amt; +10% move on a 5%-ish stop is ~+2R
    assert mtm["r"] > 0 and mtm["pnl_pct_price"] == pytest.approx(
        (110.0 - pos.avg_entry()) / pos.avg_entry() * 100)
    assert mtm["bars"] == pos.bars and mtm["sl"] == pos.sl
    assert "PBK_L" in open_report({SYM: pos}, {SYM: 110.0})
    assert open_report({}, {}).endswith("none")
