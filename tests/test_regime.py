import numpy as np
import pandas as pd

from fabletradebot.config import test_config as make_cfg
from fabletradebot.regime import (
    build_features, TREND_UP, TREND_DOWN, CHOP, SQUEEZE, CRISIS, WARMUP)


def _mk_df(ret, vol_wick=0.002, seed=0, start="2025-01-01"):
    rng = np.random.default_rng(seed)
    n = len(ret)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol_wick, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol_wick, n)))
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "volume": np.ones(n)})
    df.index = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    return df


def test_trend_detected():
    cfg = make_cfg()
    rng = np.random.default_rng(3)
    ret = 0.001 + rng.normal(0, 0.002, 1200)  # strong persistent drift
    feats = build_features(_mk_df(ret), None, cfg)
    tail = feats["regime"].iloc[-200:]
    assert (tail == TREND_UP).mean() > 0.6


def test_chop_not_trend():
    cfg = make_cfg()
    rng = np.random.default_rng(4)
    ret = rng.normal(0, 0.004, 1200)  # driftless noise
    feats = build_features(_mk_df(ret), None, cfg)
    tail = feats["regime"].iloc[-300:]
    assert (tail.isin([CHOP, SQUEEZE])).mean() > 0.7


def test_crisis_immediate_on_crash():
    cfg = make_cfg()
    rng = np.random.default_rng(5)
    ret = rng.normal(0, 0.003, 1200)
    ret[900:] = rng.normal(0, 0.012, 300)  # vol explodes to push v_pct high
    ret[1000] = -0.10                      # crash bar
    feats = build_features(_mk_df(ret), None, cfg)
    assert feats["regime"].iloc[1000] == CRISIS  # no hysteresis delay into CRISIS


def test_hysteresis_blocks_one_bar_flicker():
    cfg = make_cfg()
    from fabletradebot import regime as rg

    # drive _classify directly with a scripted raw-regime sequence
    seq = [CHOP] * 10 + [TREND_UP] + [CHOP] * 10  # single-bar flicker
    rows = pd.DataFrame({k: 1.0 for k in rg.CORE_FEATURES + ["ret_sigma", "volvol_pct"]},
                        index=range(len(seq)))
    orig = rg._raw_regime
    it = iter(seq)
    rg._raw_regime = lambda row, cfg: next(it)
    try:
        out = rg._classify(rows, cfg)
    finally:
        rg._raw_regime = orig
    assert TREND_UP not in out[1:]  # flicker never confirmed


def test_warmup_early_bars():
    cfg = make_cfg()
    rng = np.random.default_rng(6)
    feats = build_features(_mk_df(rng.normal(0, 0.003, 400)), None, cfg)
    assert feats["regime"].iloc[0] == WARMUP
