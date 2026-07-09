import pandas as pd

from fabletradebot.config import test_config as make_cfg
from fabletradebot.risk import RiskManager, OK, HALTED, HARD_STOP


def test_full_qty_formula_and_leverage_cap():
    cfg = make_cfg()
    rm = RiskManager(cfg)
    # r_eff = 0.0075 * 1.25 (TREND) * 1.0 (perf) * 1.0 (BTC liq) = 0.009375
    q = rm.full_qty(100_000, "TREND_UP", "BTC", entry=100.0, stop=99.0)
    assert abs(q - 937.5) < 1e-9
    # tiny stop distance -> leverage cap binds: 5x * 100k / 100 = 5000
    q = rm.full_qty(100_000, "TREND_UP", "BTC", entry=100.0, stop=99.99)
    assert abs(q - 5000.0) < 1e-9
    # CRISIS -> zero size
    assert rm.full_qty(100_000, "CRISIS", "BTC", 100.0, 99.0) == 0.0
    # HYPE gets liquidity haircut 0.5 and 1.5x cap
    q = rm.full_qty(100_000, "SQUEEZE", "HYPE", 100.0, 99.0)
    assert abs(q - 0.0075 * 1.0 * 0.5 * 100_000 / 1.0) < 1e-9


def test_m_perf_anti_martingale():
    cfg = make_cfg()
    rm = RiskManager(cfg)
    assert rm.m_perf() == 1.0  # too few trades -> neutral
    for _ in range(10):
        rm.record_trade(-0.5)
    assert rm.m_perf() == cfg.m_perf_lo  # losing streak -> half size
    for _ in range(20):
        rm.record_trade(+0.5)
    assert rm.m_perf() == cfg.m_perf_hi  # winning -> 1.25


def test_daily_breaker_and_hard_stop():
    cfg = make_cfg()
    rm = RiskManager(cfg)
    t0 = pd.Timestamp("2025-06-02 00:00", tz="UTC")
    assert rm.on_bar(t0, 100_000) == OK
    assert rm.on_bar(t0 + pd.Timedelta(hours=3), 96_900) == HALTED   # -3.1% day
    assert rm.on_bar(t0 + pd.Timedelta(hours=10), 96_900) == HALTED  # still inside halt
    # next day, fresh anchor
    assert rm.on_bar(t0 + pd.Timedelta(hours=28), 96_900) == OK
    # -16% from HWM -> permanent hard stop
    assert rm.on_bar(t0 + pd.Timedelta(hours=30), 84_000) == HARD_STOP
    assert rm.on_bar(t0 + pd.Timedelta(hours=31), 100_000) == HARD_STOP


def test_portfolio_beta_cap_and_same_direction_limit():
    cfg = make_cfg()
    rm = RiskManager(cfg)
    marks = {"BTC": 100.0, "ETH": 100.0, "SOL": 100.0, "HYPE": 100.0}
    # beta cap: existing BTC notional 150k (beta 1.0); adding 60k SOL (beta 1.5)
    # -> 150k + 90k = 240k > 2.0 * 100k -> rejected
    positions = {"BTC": (1, 1500.0), "ETH": None, "SOL": None, "HYPE": None}
    assert not rm.portfolio_ok(positions, "SOL", 1, 60_000 * 1.0, marks, 100_000)
    assert rm.portfolio_ok(positions, "SOL", 1, 20_000, marks, 100_000)
    # same-direction cap: 3 longs already -> 4th long rejected, short fine
    positions = {"BTC": (1, 10.0), "ETH": (1, 10.0), "SOL": (1, 10.0), "HYPE": None}
    assert not rm.portfolio_ok(positions, "HYPE", 1, 1_000, marks, 100_000)
    assert rm.portfolio_ok(positions, "HYPE", -1, 1_000, marks, 100_000)
