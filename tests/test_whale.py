"""Whale mode (V4): single full-margin position on the highest-confidence coin."""
import pandas as pd
import pytest

from fabletradebot.config import Params, profile
from fabletradebot.engine import run
from fabletradebot.risk import conf_tier, final_leverage, size_position


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


def test_whale_liq_safety_caps_leverage_on_wide_stop():
    # confidence wants 10x but an 8% stop would liquidate first -> capped to 3x
    p = profile("whale")
    lev, _ = final_leverage(0.85, 0.08, "TREND_UP", 10.0, p)
    assert lev == 3.0


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
