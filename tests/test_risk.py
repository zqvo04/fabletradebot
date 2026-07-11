import pytest

from fabletradebot.config import Params
from fabletradebot.risk import (conf_tier, final_leverage, floor_tier,
                                lev_liq_cap, size_position)

P = Params()


def test_conf_tier_mapping():
    assert conf_tier(0.59, P) == (0.0, 0.0)
    assert conf_tier(0.60, P) == (2.0, 0.005)
    assert conf_tier(0.75, P) == (3.0, 0.008)
    assert conf_tier(0.85, P) == (5.0, 0.011)
    assert conf_tier(0.95, P) == (10.0, 0.015)


def test_liq_cap_tightens_with_wider_stop():
    # 2% stop -> 1/(0.06+0.015)=13.3 -> 10x allowed
    assert floor_tier(lev_liq_cap(0.02, P)) == 10.0
    # 4% stop -> 7.4 -> 5x
    assert floor_tier(lev_liq_cap(0.04, P)) == 5.0
    # 8% stop -> 3.9 -> 3x
    assert floor_tier(lev_liq_cap(0.08, P)) == 3.0
    # 20% stop -> 1.6 -> no tier qualifies
    assert floor_tier(lev_liq_cap(0.20, P)) == 0.0


def test_final_leverage_is_min_of_all_caps():
    # high conf, tight stop, TREND, but alt capped at 3x
    lev, risk = final_leverage(0.95, 0.02, "TREND", 3.0, P)
    assert (lev, risk) == (3.0, 0.015)
    # high conf, tight stop, BTC 10x, but RANGE regime caps at 5x
    lev, _ = final_leverage(0.95, 0.02, "RANGE", 10.0, P)
    assert lev == 5.0
    # CRISIS blocks everything
    assert final_leverage(0.95, 0.02, "CRISIS", 10.0, P)[0] == 0.0


def test_fixed_risk_sizing_and_liq_beyond_stop():
    sz = size_position(10_000, 0.01, entry=100.0, sl=98.0, direction=1, leverage=5.0)
    assert sz.risk_amt == pytest.approx(100.0)            # 1% of equity
    assert sz.notional == pytest.approx(100.0 / 0.02)     # risk / stop_frac
    assert sz.margin == pytest.approx(sz.notional / 5.0)
    # liq price must be strictly beyond the stop
    assert sz.liq_price < 98.0
    sz_s = size_position(10_000, 0.01, entry=100.0, sl=102.0, direction=-1, leverage=5.0)
    assert sz_s.liq_price > 102.0


def test_notional_cap_binds_risk_below_nominal():
    # stop 0.05% with r=1% would want 20x equity notional; 10x cap binds
    sz = size_position(10_000, 0.01, entry=100.0, sl=99.95, direction=1, leverage=10.0)
    assert sz.notional == pytest.approx(100_000.0)
    assert sz.risk_amt == pytest.approx(100_000.0 * 0.0005)


def test_liq_safety_assertion_fires_when_violated():
    # 10x leverage with a 15% stop: liq (~9%) sits inside the stop -> must raise
    with pytest.raises(AssertionError):
        size_position(10_000, 0.01, entry=100.0, sl=85.0, direction=1, leverage=10.0)
