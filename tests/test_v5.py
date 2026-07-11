"""v5 features: maker execution, portfolio vol cap, satellite universe
gating, carry sleeve neutrality, and the account-leverage plan."""
import numpy as np
import pandas as pd

from fabletradebot.leverage_plan import (plan_leverage, safety_max_leverage)
from fabletradebot.v3 import (V3Backtester, universe_mask, target_weights,
                              v3_config, v4_config, v5_config)


def test_v5_config_frozen():
    cfg = v5_config()
    # 0.30 is the return-frontier peak but FAILED the risk gates (MC 95%
    # MDD -30.2%); the gates pick the deployable seat, as for v3/v4
    assert cfg.vol_budget == 0.20 and cfg.port_vol_cap == 0.20
    assert cfg.exec_maker is True
    assert cfg.dd_soft == -0.06 and cfg.dd_hard == -0.15  # bands scale w/ vol
    assert cfg.stress_limit == 0.25         # liquidation guard kept from v4
    assert cfg.xs_min_assets == 4
    assert cfg.conv_enabled is False        # conviction SIZING: still dead
    assert cfg.w_carry == 0.0               # carry sleeve awaits funding data
    assert cfg.lev_cap == v3_config().lev_cap   # tight core ceilings untouched
    assert len(cfg.satellites) == 20 and set(cfg.sat_list_time) == set(cfg.satellites)
    assert cfg.sat_lev_cap == 0.5           # HYPE-class caution for satellites


def _panel(n=600, seed=3, assets=("BTC", "ETH", "SOL", "HYPE"),
           drifts=(0.002, -0.002, 0.0, 0.0), vol=0.008, volume=1000.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    data = {}
    for a, mu in zip(assets, drifts):
        r = rng.normal(mu, vol, n)
        c = 100.0 * np.exp(np.cumsum(r))
        o = np.concatenate([[100.0], c[:-1]])
        data[a] = pd.DataFrame(
            {"open": o, "high": np.maximum(o, c) * 1.001,
             "low": np.minimum(o, c) * 0.999, "close": c,
             "volume": np.full(n, volume)}, index=idx)
    return data


def _v5ish(**kw):
    """v4 risk profile + the v5 feature switches under test."""
    cfg = v4_config()
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_maker_exec_cuts_costs_same_signal():
    data = _panel()
    taker = V3Backtester(data, _v5ish()).run()
    maker = V3Backtester(data, _v5ish(exec_maker=True)).run()
    assert maker.stats["fees"] < taker.stats["fees"] * 0.6
    # signal/decisions identical — only execution differs
    assert np.allclose(maker.weights.to_numpy(), taker.weights.to_numpy(),
                       atol=1e-12)


def test_no_lookahead_with_all_v5_features():
    """Truncation invariance must hold with maker exec, port cap, satellites
    and carry all switched on at once."""
    assets = ("BTC", "ETH", "SOL", "HYPE", "NEW1", "NEW2")
    drifts = (0.002, -0.002, 0.0, 0.0, 0.001, -0.001)
    data = _panel(assets=assets, drifts=drifts, volume=3e6)
    idx = data["BTC"].index
    funding = {a: pd.Series(1e-4, index=idx[::2]) for a in assets}
    cfg = _v5ish(exec_maker=True, port_vol_cap=0.40, w_carry=0.2,
                 satellites=("NEW1", "NEW2"),
                 sat_list_time={"NEW1": "2025-01-01T00:00:00+00:00",
                                "NEW2": "2025-01-01T00:00:00+00:00"},
                 sat_vol_floor=1e6, sat_vol_drop=5e5, sat_vol_win=30)
    full = V3Backtester(data, cfg, funding=funding).run()
    cut = {a: df.iloc[:-50] for a, df in data.items()}
    part = V3Backtester(cut, cfg, funding=funding).run()
    overlap = part.weights.index[:-1]
    assert np.allclose(full.weights.loc[overlap].to_numpy(),
                       part.weights.loc[overlap].to_numpy(), atol=1e-12)


def test_universe_mask_age_volume_hysteresis():
    n = 400
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    vol = np.full(n, 5e5)          # ~ $3.0M/day est. -> below the $4M floor
    vol[100:200] = 1e6             # ~ $6.0M/day     -> enrols
    vol[200:] = 7e5                # ~ $4.2M/day     -> between drop & floor
    c = np.full(n, 1.0)
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                       "volume": vol}, index=idx)
    cfg = _v5ish(satellites=("SAT",),
                 sat_list_time={"SAT": "2025-01-01T00:00:00+00:00"},
                 sat_age_min_days=10.0, sat_vol_win=12,
                 sat_vol_floor=4e6, sat_vol_drop=3e6)
    m = universe_mask({"SAT": df}, cfg)["SAT"].to_numpy()
    age_bars = int(10 * 6)
    assert not m[:age_bars].any()          # too young no matter the volume
    assert not m[age_bars:100].any()       # old enough but below the floor
    assert m[150:200].all()                # enrolled: floor crossed
    assert m[250:].all()                   # hysteresis: stays above drop line
    # ...and a hard fade below the drop line de-enrols
    vol2 = vol.copy(); vol2[300:] = 1e5
    df2 = df.assign(volume=vol2)
    m2 = universe_mask({"SAT": df2}, cfg)["SAT"].to_numpy()
    assert m2[250:300].all() and not m2[320:].any()


def test_satellite_safety_class_defaults():
    cfg = _v5ish(satellites=("NEW1",))
    assert cfg.lev("NEW1") == cfg.sat_lev_cap
    assert cfg.beta_of("NEW1") == cfg.sat_beta
    assert cfg.slip("NEW1") == cfg.sat_slip_bps
    assert cfg.lev("BTC") == cfg.lev_cap["BTC"]     # core untouched


def test_port_vol_cap_scales_book_down():
    data = _panel()
    free = V3Backtester(data, _v5ish()).run()
    capped = V3Backtester(data, _v5ish(port_vol_cap=0.02)).run()  # absurdly low
    g_free = free.weights.abs().sum(axis=1)
    g_cap = capped.weights.abs().sum(axis=1)
    live = g_free > 1e-6
    assert (g_cap[live] <= g_free[live] + 1e-12).all()
    assert g_cap[live].mean() < 0.5 * g_free[live].mean()


def test_carry_missing_degrades_neutral():
    data = _panel()
    base = V3Backtester(data, _v5ish()).run()
    carry_no_data = V3Backtester(data, _v5ish(w_carry=0.5)).run()
    assert np.allclose(base.weights.to_numpy(),
                       carry_no_data.weights.to_numpy(), atol=1e-12)


def test_carry_tilts_against_positive_funding():
    row = {a: {"tsm": 0.0, "mr": 0.0, "xs": 0.0, "carry": c, "vol_ann": 0.5}
           for a, c in zip(("BTC", "ETH", "SOL", "HYPE"), (1.0, -1.0, 0.0, 0.0))}
    cfg = _v5ish(w_carry=0.3)
    w = target_weights(row, 0.0, cfg)
    assert w["BTC"] > 0 and w["ETH"] < 0 and w["SOL"] == 0


def test_leverage_plan_tiers_and_safety_cap():
    cfg = v4_config()
    row = {
        "BTC": {"xs": 0.9, "agree": 1.0, "disp_pct": 90.0, "vol_ann": 0.45},
        "HYPE": {"xs": -0.5, "agree": 1.0, "disp_pct": 90.0, "vol_ann": 1.4},
        "SOL": {"xs": 0.15, "agree": np.nan, "disp_pct": np.nan, "vol_ann": 0.8},
    }
    w = {"BTC": 0.85, "HYPE": -0.28, "SOL": 0.11}
    p = plan_leverage(w, row, 0.0, cfg)
    # high conviction asks 10x, 45%-vol safety cap (9.6x) clips to the 5x tier
    assert p["assets"]["BTC"]["tier"] == 5.0 and p["assets"]["BTC"]["safety_bound"]
    # HYPE at 140% vol: liquidation must stay >= 4 daily sigmas away -> 3x
    assert p["assets"]["HYPE"]["tier"] == 3.0
    assert p["assets"]["HYPE"]["liq_sigmas"] >= 4.0
    # weak signal -> floor tier regardless of vol
    assert p["assets"]["SOL"]["tier"] == 2.0
    # margin math: |w| / tier, summed
    exp_margin = 0.85 / 5 + 0.28 / 3 + 0.11 / 2
    assert abs(p["total_margin_frac"] - exp_margin) < 1e-3
    # a deep drawdown cuts confidence through the same governor the book uses
    p_dd = plan_leverage(w, row, -0.29, cfg)
    assert p_dd["assets"]["BTC"]["tier"] <= 3.0
    assert p_dd["governor_mult"] < 0.35


def test_safety_leverage_monotone_in_vol():
    assert safety_max_leverage(0.3) > safety_max_leverage(0.8) > \
        safety_max_leverage(2.0)
    assert safety_max_leverage(2.5) < 2.0   # violent names can't even hold 2x
