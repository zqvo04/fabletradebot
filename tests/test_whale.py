"""Whale mode (V4): single full-margin position on the highest-confidence coin."""
import numpy as np
import pandas as pd
import pytest

from fabletradebot.config import Params, profile
from fabletradebot.engine import run
from fabletradebot.risk import conf_tier, final_leverage, size_position
from fabletradebot.signals import hold_confidence


def test_whale_profile_wiring():
    p = profile("whale")
    assert p.whale_mode is True
    assert p.max_positions == 1 and p.max_positions_corr == 1
    assert p.pyramid_max == 0 and p.aggression_syms == ()


def test_whale_confidence_leverage_tiers():
    p = profile("whale")
    assert conf_tier(0.54, p) == (0.0, 0.0)      # below entry
    assert conf_tier(0.55, p)[0] == 2.0
    assert conf_tier(0.62, p)[0] == 3.0
    assert conf_tier(0.70, p)[0] == 5.0
    assert conf_tier(0.80, p)[0] == 10.0
    assert conf_tier(0.95, p)[0] == 10.0


def test_full_margin_sizing():
    # full margin: notional = equity*lev, margin == equity, risk = the real
    # stop-out loss (equity*lev*stop_frac), not a fixed risk budget
    sz = size_position(10_000, 0.005, entry=100.0, sl=98.0, direction=1,
                       leverage=5.0, full_margin=True)
    assert sz.notional == pytest.approx(50_000.0)
    assert sz.margin == pytest.approx(10_000.0)
    assert sz.risk_amt == pytest.approx(50_000.0 * 0.02)   # 1000 = lev*stop of equity
    assert sz.liq_price < 98.0                             # liquidation beyond the stop


def test_full_margin_de_risks_with_margin_frac():
    # margin_frac<1 (dd_half / correlation halving in whale mode) scales the
    # deployed margin, halving risk, WITHOUT changing leverage or liq distance
    full = size_position(10_000, 0.005, entry=100.0, sl=98.0, direction=1,
                         leverage=5.0, full_margin=True, margin_frac=1.0)
    half = size_position(10_000, 0.005, entry=100.0, sl=98.0, direction=1,
                         leverage=5.0, full_margin=True, margin_frac=0.5)
    assert half.notional == pytest.approx(full.notional * 0.5)
    assert half.margin == pytest.approx(5_000.0)          # half the account
    assert half.risk_amt == pytest.approx(full.risk_amt * 0.5)
    assert half.leverage == full.leverage                 # leverage untouched
    assert half.liq_price == pytest.approx(full.liq_price)  # liq distance untouched


def test_whale_liq_safety_caps_leverage_on_wide_stop():
    # confidence wants 10x but an 8% stop would liquidate first -> capped to 3x
    p = profile("whale")
    lev, _ = final_leverage(0.85, 0.08, "TREND_UP", 10.0, p)
    assert lev == 3.0


def test_whale_drawdown_governor_halves_deployed_margin():
    # Engine-level proof that dd_half now works in whale mode: seed a carry
    # sitting at a 15% drawdown (peak 10k, cash 8.5k) with no open position,
    # then fire a candidate — the new position must deploy HALF the account.
    idx = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")
    df = pd.DataFrame([(100, 101, 99.5, 100)] * 4,
                      columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    f = pd.DataFrame(index=idx)
    f["atr1h"], f["bias4h"] = 1.0, 1.0
    f["hold_L"], f["hold_S"] = 0.9, 0.0
    cands = {"BTC": pd.DataFrame({"dir": [1], "conf": [0.85], "sl": [95.0],
                                 "setup": ["BRK_L"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    carry = {"cash": 8_500.0, "peak": 10_000.0, "dd_frozen": False,
             "circuit_until": None, "loss_log": [], "cooldown": {},
             "positions": {}, "pendings": []}
    res = run({"BTC": df}, {"BTC": f}, cands, {"BTC": None}, regime, corr,
              profile("whale"), start=idx[0], carry=carry)
    pos = res["open_positions"]["BTC"]
    # dd = 15% >= dd_half(10%) -> mult 0.5 -> margin is HALF of equity (~8500)
    assert pos.margin == pytest.approx(8_500.0 * 0.5, rel=1e-6)
    assert pos.notional == pytest.approx(pos.margin * pos.leverage, rel=1e-6)


def _two_coin_frames(conf_btc, conf_eth):
    """BTC and ETH each fire a candidate at bar0; flat path (no stop hit)."""
    idx = pd.date_range("2024-01-01", periods=8, freq="1h", tz="UTC")
    bar = [100, 101, 99.5, 100]
    frames, features, cands = {}, {}, {}
    for sym, conf in (("BTC", conf_btc), ("ETH", conf_eth)):
        df = pd.DataFrame([bar] * len(idx), columns=["open", "high", "low", "close"],
                          index=idx)
        df["volume"] = 1000.0
        frames[sym] = df
        f = pd.DataFrame(index=idx)
        f["atr1h"], f["bias4h"] = 1.0, 1.0
        features[sym] = f
        cands[sym] = pd.DataFrame({"dir": [1], "conf": [conf], "sl": [95.0],
                                   "setup": ["BRK_L"]}, index=[idx[0]])
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    return frames, features, cands, {"BTC": None, "ETH": None}, regime, corr


def test_whale_picks_highest_confidence_coin_single_position():
    # ETH is the higher-confidence signal -> it claims the only seat; BTC is
    # left out even though its candidate fired the same bar.
    args = _two_coin_frames(conf_btc=0.65, conf_eth=0.85)
    res = run(*args, profile("whale"), equity0=10_000.0)
    open_pos = res["open_positions"]
    assert list(open_pos) == ["ETH"]
    pos = open_pos["ETH"]
    # full-margin: whole account is the margin
    assert pos.margin == pytest.approx(10_000.0, rel=1e-6)
    assert pos.notional == pytest.approx(pos.margin * pos.leverage, rel=1e-6)


def test_whale_holds_position_no_cross_coin_switch():
    # A stronger BTC signal arriving later must NOT displace the held ETH seat.
    frames, features, cands, funding, regime, corr = _two_coin_frames(0.60, 0.80)
    idx = frames["BTC"].index
    # BTC fires an even higher-confidence signal two bars later
    cands["BTC"] = pd.DataFrame({"dir": [1], "conf": [0.95], "sl": [95.0],
                                 "setup": ["BRK_L"]}, index=[idx[2]])
    res = run(frames, features, cands, funding, regime, corr,
              profile("whale"), equity0=10_000.0)
    # ETH still holds the seat, BTC never entered
    assert list(res["open_positions"]) == ["ETH"]
    assert len(res["trades"]) == 0


# ---- momentum / confidence-fade management (V4) ----

def test_hold_confidence_high_when_aligned_low_when_not():
    idx = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    f = pd.DataFrame({"bias1d": 1.0, "bias4h": 1.0, "close": 110.0,
                      "ema20_4h": 100.0, "atr4h": 5.0, "rsi4h": 70.0}, index=idx)
    btc = pd.Series(1.0, index=idx)
    up = hold_confidence(f, pd.Series("TREND_UP", index=idx), btc, 1, Params())
    assert up.iloc[0] == pytest.approx(1.0)          # every read favourable
    # fully against a long: opposite trend, below EMA20, weak RSI
    f2 = f.assign(bias1d=-1.0, bias4h=-1.0, close=90.0, rsi4h=30.0)
    dn = hold_confidence(f2, pd.Series("TREND_DOWN", index=idx),
                         pd.Series(-1.0, index=idx), 1, Params())
    assert dn.iloc[0] == pytest.approx(0.0)


# fade params on standard (risk-based) sizing so the R arithmetic is simple:
# notional 1000, risk_amt 50 -> price 110 ~ +2R, 108 ~ +1.6R, 105 ~ +1R
_FADE_P = Params(aggression_syms=(), hold_conf_exit=0.50,
                 hold_conf_bars=2, hold_conf_min_r=1.0, hold_giveback=0.5)


def _run_fade(path, hold_seq, p):
    idx = pd.date_range("2024-01-01", periods=len(path), freq="1h", tz="UTC")
    df = pd.DataFrame(path, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    f = pd.DataFrame(index=idx)
    f["atr1h"], f["bias4h"] = 1.0, 1.0          # bias aligned -> no BiasFlip exit
    f["hold_L"], f["hold_S"] = hold_seq, 0.0
    cands = {"BTC": pd.DataFrame({"dir": [1], "conf": [0.85], "sl": [95.0],
                                 "setup": ["BRK_L"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    return run({"BTC": df}, {"BTC": f}, cands, {"BTC": None}, regime, corr, p,
               equity0=10_000.0)


def test_signalfade_banks_winner_on_giveback():
    # runs to ~+2R (110) then gives back half to ~+1R (105) -> lock it in
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 110.5, 99.9, 110), (110, 110, 103, 105), (105, 105, 104, 105)]
    res = _run_fade(path, [0.9] * 5, _FADE_P)
    assert len(res["trades"]) == 1
    tr = res["trades"].iloc[0]
    assert tr["reason"] == "SignalFade" and tr["pnl"] > 0


def test_signalfade_banks_winner_on_conviction_collapse():
    # up ~+1.6R and holding (no give-back), but conviction drops for 2 bars
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 108.5, 99.9, 108), (108, 108.2, 107, 108),
            (108, 108.2, 107, 108), (108, 108.2, 107, 108)]
    res = _run_fade(path, [0.9, 0.9, 0.9, 0.2, 0.2, 0.9], _FADE_P)
    assert len(res["trades"]) == 1
    tr = res["trades"].iloc[0]
    assert tr["reason"] == "SignalFade" and tr["pnl"] > 0


def test_signalfade_never_banks_a_loser():
    # underwater with collapsed conviction -> the stop's job, not the fade's
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 100, 97, 97), (97, 97.5, 96.5, 97), (97, 97.5, 96.5, 97)]
    res = _run_fade(path, [0.2] * 5, _FADE_P)
    assert "SignalFade" not in list(res["trades"].get("reason", []))
    assert list(res["open_positions"]) == ["BTC"]


def test_signalfade_disabled_when_hold_conf_exit_zero():
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 110.5, 99.9, 110), (110, 110, 103, 105), (105, 105, 104, 105)]
    res = _run_fade(path, [0.9] * 5, Params(aggression_syms=()))  # fade off
    assert "SignalFade" not in list(res["trades"].get("reason", []))


# ---- losing-position early cut (V5) ----

# winner-fade off, loss-fade armed at a floor below the neutral 0.50
_LOSS_P = Params(aggression_syms=(), hold_conf_exit=0.0, hold_loss_exit=0.40,
                 hold_conf_bars=2)


def test_lossfade_cuts_loser_on_conviction_collapse():
    # long fills ~100; price drifts underwater but never reaches the 95 stop,
    # while live conviction collapses for 2 consecutive bars -> cut early
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 100, 98, 98.5), (98.5, 98.7, 97.5, 98),
            (98, 98.3, 97.4, 98)]
    res = _run_fade(path, [0.9, 0.9, 0.2, 0.2, 0.2], _LOSS_P)
    assert len(res["trades"]) == 1
    tr = res["trades"].iloc[0]
    assert tr["reason"] == "LossFade"
    assert tr["pnl"] < 0                 # a loss — but banked before the full SL
    assert tr["r"] > -1.0                # smaller than a stop-out (~ -1R)


def test_lossfade_needs_consecutive_collapse():
    # conviction dips one bar then recovers -> streak resets, no early cut
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 100, 98, 98.5), (98.5, 99, 97.5, 98.5),
            (98.5, 99, 98, 98.5)]
    res = _run_fade(path, [0.9, 0.9, 0.2, 0.9, 0.9], _LOSS_P)
    assert "LossFade" not in list(res["trades"].get("reason", []))
    assert list(res["open_positions"]) == ["BTC"]


def test_lossfade_leaves_winner_to_the_winner_exit():
    # collapsed conviction but the trade is in PROFIT -> LossFade must not fire
    # (SignalFade owns winners; here winner-fade is off, so it simply holds)
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 106, 99.9, 105), (105, 106, 104, 105), (105, 106, 104, 105)]
    res = _run_fade(path, [0.9, 0.9, 0.2, 0.2, 0.2], _LOSS_P)
    assert "LossFade" not in list(res["trades"].get("reason", []))
    assert list(res["open_positions"]) == ["BTC"]


def test_lossfade_disabled_by_default():
    # default params never arm the loss cut -> underwater position rides to the
    # stop's job, exactly as before this feature existed
    path = [(100, 100.5, 99.5, 100), (100, 100.5, 99.8, 100),
            (100, 100, 98, 98.5), (98.5, 98.7, 97.5, 98),
            (98, 98.3, 97.4, 98)]
    res = _run_fade(path, [0.2] * 5, Params(aggression_syms=()))
    assert "LossFade" not in list(res["trades"].get("reason", []))
    assert list(res["open_positions"]) == ["BTC"]
