import numpy as np
import pandas as pd

from fabletradebot import indicators as ind


def test_efficiency_ratio_extremes():
    up = pd.Series(np.arange(1.0, 101.0))
    er = ind.efficiency_ratio(up, 24)
    assert er.iloc[-1] > 0.99  # straight line -> 1

    osc = pd.Series([100.0, 101.0] * 50)
    er = ind.efficiency_ratio(osc, 24)
    assert er.iloc[-1] < 0.1  # pure oscillation -> ~0


def test_ols_tstat_sign_and_magnitude():
    n = 200
    up = pd.Series(np.linspace(0, 1, n) + np.random.default_rng(0).normal(0, 0.01, n))
    t = ind.ols_tstat(up, 48)
    assert t.iloc[-1] > 2.0
    down = -up
    assert ind.ols_tstat(down, 48).iloc[-1] < -2.0


def test_pct_rank_bounds():
    s = pd.Series(np.arange(100.0))
    pr = ind.pct_rank(s, 50)
    assert pr.iloc[-1] == 100.0  # newest value is the max of its window
    assert pr.dropna().between(0, 100).all()


def test_atr_positive_and_donchian_excludes_current_bar():
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 300))
    df = pd.DataFrame({
        "open": c, "high": c + 1, "low": c - 1, "close": c,
        "volume": np.ones(300),
    })
    atr = ind.atr(df, 14)
    assert (atr.dropna() > 0).all()
    hi, lo = ind.donchian(df, 48)
    # current bar's high must not be inside its own channel value
    i = 200
    assert hi.iloc[i] == df["high"].iloc[i - 48:i].max()


def test_winsorize_clips_only_extremes():
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0, 0.01, 500))
    r.iloc[400] = 0.5  # 50-sigma outlier
    w = ind.winsorize_returns(r, 5.0)
    assert w.iloc[400] < 0.5
    assert np.allclose(w.iloc[300], r.iloc[300])
