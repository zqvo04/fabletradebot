"""v4 aggressive profile: frozen fields, stress cap, tier machinery, governor."""
import numpy as np

from fabletradebot.v3 import (V3Config, _tier, conviction, target_weights,
                              v3_config, v4_config)


def _row(xs=0.5, vol=0.5, agree=np.nan, disp=np.nan):
    return {"tsm": 0.0, "mr": 0.0, "xs": xs, "agree": agree,
            "disp_pct": disp, "vol_ann": vol}


def test_v4_config_frozen():
    cfg = v4_config()
    assert cfg.vol_budget == 0.40
    assert cfg.stress_limit == 0.25
    assert cfg.dd_soft == -0.12 and cfg.dd_hard == -0.30
    assert cfg.xs_min_assets == 4          # full panel only (ASSESSMENT #7)
    assert cfg.conv_enabled is False       # conviction tiers: tested, rejected
    assert cfg.lev_cap == v3_config().lev_cap  # tight ceilings are load-bearing


def test_stress_cap_bounds_correlated_shock():
    cfg = v4_config()
    row = {a: _row(xs=1.0, vol=1.5) for a in cfg.assets}  # extreme vol book
    w = target_weights(row, 0.0, cfg)
    stress = sum(abs(v) * row[a]["vol_ann"] / np.sqrt(365.0) * 3.0
                 for a, v in w.items())
    assert stress <= cfg.stress_limit + 1e-9


def test_v4_scales_v3_weights_double():
    """Same signal, 2x budget -> exactly 2x weights while no cap binds."""
    row = {a: _row(xs=0.3, vol=0.6) for a in ("BTC", "ETH", "SOL", "HYPE")}
    w3 = target_weights(row, 0.0, v3_config())
    w4 = target_weights(row, 0.0, v4_config())
    for a in w3:
        assert abs(w4[a] - 2.0 * w3[a]) < 1e-9


def test_governor_bands_scaled():
    cfg = v4_config()
    row = {a: _row() for a in cfg.assets}
    w_ok = target_weights(row, -0.10, cfg)     # inside soft band: untouched
    w_ref = target_weights(row, 0.0, cfg)
    assert all(abs(w_ok[a] - w_ref[a]) < 1e-9 for a in cfg.assets)
    w_dd = target_weights(row, -0.30, cfg)     # at hard line -> floor x0.25
    assert all(abs(w_dd[a] - cfg.dd_floor * w_ref[a]) < 1e-9 for a in cfg.assets)


def test_tier_lookup_order():
    cfg = V3Config()
    assert _tier(0.95, cfg) == (10.0, 4.0)
    assert _tier(0.70, cfg) == (5.0, 2.5)
    assert _tier(0.40, cfg) == (3.0, 1.5)
    assert _tier(0.10, cfg) == (2.0, 1.0)


def test_conviction_bounds_and_neutral_degradation():
    cfg = V3Config()
    assert conviction(_row(xs=0.0), cfg) == 0.0
    full = conviction(_row(xs=1.0, agree=1.0, disp=100.0), cfg)
    assert abs(full - 1.0) < 1e-9
    # missing agree/disp degrade to the neutral 0.5 factor, not to 0 or 1
    mid = conviction(_row(xs=1.0), cfg)
    assert abs(mid - 0.5625) < 1e-9            # 1.0 * 0.75 * 0.75
