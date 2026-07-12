"""Full-pipeline mechanism test on a synthetic universe (G1)."""
import os

import pandas as pd
import pytest

from fabletradebot.backtest import metrics, prepare
from fabletradebot.config import Params
from fabletradebot.engine import run
from fabletradebot.synthetic import make_1h, make_funding


@pytest.fixture(scope="module")
def synth_universe():
    frames, funding = {}, {}
    for i, sym in enumerate(["BTC", "ETH", "SOL"]):
        df = make_1h(6000, seed=10 + i, regime_switch=True, vol=0.008)
        frames[sym] = df
        funding[sym] = make_funding(df.index, seed=20 + i)
    return frames, funding


def test_pipeline_runs_and_respects_limits(synth_universe):
    frames, funding = synth_universe
    p = Params()
    features, candidates, regime_h, corr = prepare(frames, funding, p)
    res = run(frames, features, candidates, funding, regime_h, corr, p,
              equity0=10_000.0)
    t = res["trades"]
    eq = res["equity"]
    assert len(eq) == 6000
    m = metrics(t, eq, 10_000.0)
    if len(t):
        # per-trade loss bounded by risk + costs (fixed-risk guarantee)
        worst = (t["pnl"] / t["risk_amt"]).min()
        assert worst > -1.6, f"trade lost far more than its risk budget: {worst}R"
        # leverage tiers only, never above asset/regime caps
        assert set(t["leverage"].unique()) <= {2.0, 3.0, 5.0, 10.0}
        assert (t["conf"] >= p.conf_entry).all()
    # equity never wiped out (survival hard-gate mechanics)
    assert eq.min() > 10_000.0 * 0.5


def test_determinism(synth_universe):
    frames, funding = synth_universe
    p = Params()
    features, candidates, regime_h, corr = prepare(frames, funding, p)
    r1 = run(frames, features, candidates, funding, regime_h, corr, p)
    r2 = run(frames, features, candidates, funding, regime_h, corr, p)
    assert r1["final_equity"] == r2["final_equity"]
    pd.testing.assert_frame_equal(r1["trades"], r2["trades"])
