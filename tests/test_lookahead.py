"""Lookahead-freedom proof (G1): decisions at time T must be identical whether
or not data after T exists. Also: engine determinism."""
import pandas as pd

from fabletradebot.config import Params
from fabletradebot.data_okx import closed_asof_1h, resample
from fabletradebot.indicators import donchian
from fabletradebot.regime import regime_1d
from fabletradebot.signals import build_features, scan
from fabletradebot.synthetic import make_1h, make_funding

P = Params()


def _candidates(df, funding, btc_1d):
    reg = regime_1d(btc_1d, P)
    reg_h = closed_asof_1h(reg, 24, df.index)
    reg_h["state"] = reg_h["state"].fillna("RANGE")
    reg_h["btc_dir"] = reg_h["btc_dir"].fillna(0)
    return scan(build_features(df, funding, P), reg_h, P)


def test_truncation_invariance_of_candidates():
    df = make_1h(3000, seed=3, regime_switch=True, vol=0.008)
    funding = make_funding(df.index, seed=3)
    btc = make_1h(3000, seed=4, regime_switch=True)
    full = _candidates(df, funding, resample(btc, 24))
    for cut in (2000, 2500, 2900):
        t_cut = df.index[cut]
        part = _candidates(df.loc[:t_cut], funding[funding.index <= t_cut + pd.Timedelta(hours=1)],
                           resample(btc.loc[:t_cut], 24))
        a = full[full.index <= part.index.max() if len(part) else t_cut]
        a = full[full.index <= t_cut]
        pd.testing.assert_frame_equal(a, part[part.index <= t_cut], check_dtype=False)


def test_donchian_uses_prior_bars_only():
    df = make_1h(500, seed=1)
    hi, _ = donchian(df, 48)
    # inflate the CURRENT bar's high massively; don_hi at that bar must not move
    df2 = df.copy()
    t = df.index[300]
    df2.loc[t, "high"] *= 10
    hi2, _ = donchian(df2, 48)
    assert hi.loc[t] == hi2.loc[t]
    # but the NEXT bar's channel must see it
    assert hi2.loc[df.index[301]] > hi.loc[df.index[301]]


def test_closed_asof_projects_only_closed_htf_bars():
    df = make_1h(200, seed=2)
    d4 = resample(df, 4)
    feat = pd.DataFrame({"x": range(len(d4))}, index=d4.index)
    proj = closed_asof_1h(feat, 4, df.index)
    # at 1H bar opening 02:00 (decision 03:00), the 00:00-04:00 4H bar is NOT
    # closed yet -> value must come from the previous day-boundary-aligned bar
    t = df.index[2]  # 02:00
    assert proj.loc[t, "x"] != feat["x"].iloc[0] or pd.isna(proj.loc[t, "x"])
    # at 1H bar opening 03:00 (decision 04:00), the first 4H bar IS closed
    t2 = df.index[3]
    assert proj.loc[t2, "x"] == feat["x"].iloc[0]


def test_regime_hysteresis_confirms_switches():
    from fabletradebot.regime import apply_hysteresis
    raw = pd.Series(["RANGE"] * 5 + ["TREND"] + ["RANGE"] * 2 + ["TREND"] * 3
                    + ["CRISIS"] + ["TREND"] * 2 + ["TREND"] * 2)
    out = apply_hysteresis(raw, confirm=2)
    assert out.iloc[5] == "RANGE"        # single TREND bar: ignored
    assert out.iloc[9] == "TREND"        # two consecutive: switched
    assert out.iloc[11] == "CRISIS"      # crisis: immediate
    assert out.iloc[12] == "CRISIS"      # needs confirm+1 to release
    assert out.iloc[14] == "TREND"
