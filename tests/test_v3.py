"""v3 continuous portfolio system: causality, accounting, sizing invariants."""
import numpy as np
import pandas as pd

from fabletradebot.v3 import V3Backtester, V3Config, target_weights, v3_config


def _panel(n=600, seed=3):
    """4-asset synthetic 4H panel: one steady riser, one steady faller,
    two noise assets. Drift is 0.25 sigma/bar so the relative-strength
    ordering dominates any single seed's noise path."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    drifts = {"BTC": 0.002, "ETH": -0.002, "SOL": 0.0, "HYPE": 0.0}
    data = {}
    for a, mu in drifts.items():
        r = rng.normal(mu, 0.008, n)
        c = 100.0 * np.exp(np.cumsum(r))
        o = np.concatenate([[100.0], c[:-1]])
        data[a] = pd.DataFrame(
            {"open": o, "high": np.maximum(o, c) * 1.001,
             "low": np.minimum(o, c) * 0.999, "close": c,
             "volume": np.full(n, 1000.0)}, index=idx)
    return data


def test_no_lookahead_truncation_invariance():
    """Weights decided at bar i must not change when future bars are removed."""
    data = _panel()
    cfg = v3_config()
    full = V3Backtester(data, cfg).run()
    cut = {a: df.iloc[:-50] for a, df in data.items()}
    part = V3Backtester(cut, cfg).run()
    overlap = part.weights.index[:-1]  # last bar's decision executes later anyway
    assert np.allclose(full.weights.loc[overlap].to_numpy(),
                       part.weights.loc[overlap].to_numpy(), atol=1e-12)


def test_xs_longs_riser_shorts_faller():
    data = _panel()
    res = V3Backtester(data, v3_config()).run()
    live = res.weights[res.weights.abs().sum(axis=1) > 0]
    assert live["BTC"].mean() > 0.02      # persistent riser held long
    assert live["ETH"].mean() < -0.02     # persistent faller held short
    assert res.stats["total_return"] > 0  # relative-strength pnl net of costs


def test_flat_market_stays_flat():
    """Identical assets -> XS demeaned to ~0 -> no positions, no fees."""
    n = 400
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    c = 100.0 + np.sin(np.arange(n) / 20.0)  # same series for every asset
    df = pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999,
                       "close": c, "volume": np.full(n, 1000.0)}, index=idx)
    data = {a: df.copy() for a in ("BTC", "ETH", "SOL", "HYPE")}
    res = V3Backtester(data, v3_config()).run()
    assert res.stats["fees"] < 1e-9
    assert abs(res.stats["total_return"]) < 1e-9


def test_gross_cap_and_lev_caps():
    cfg = v3_config()
    row = {a: {"tsm": 0.0, "mr": 0.0, "xs": 1.0, "vol_ann": 0.05}  # tiny vol
           for a in cfg.assets}
    w = target_weights(row, 0.0, cfg)
    for a, v in w.items():
        assert abs(v) <= cfg.lev_cap[a] + 1e-9
    gross_beta = sum(abs(v) * cfg.beta[a] for a, v in w.items())
    assert gross_beta <= cfg.gross_cap + 1e-9


def test_drawdown_governor_derisk():
    cfg = v3_config()
    row = {a: {"tsm": 0.0, "mr": 0.0, "xs": 0.5, "vol_ann": 0.5}
           for a in cfg.assets}
    w_ok = target_weights(row, 0.0, cfg)
    w_dd = target_weights(row, cfg.dd_hard, cfg)     # at the hard line
    for a in cfg.assets:
        assert abs(w_dd[a] - cfg.dd_floor * w_ok[a]) < 1e-9
    w_mid = target_weights(row, (cfg.dd_soft + cfg.dd_hard) / 2, cfg)
    for a in cfg.assets:
        assert abs(w_ok[a]) > abs(w_mid[a]) > abs(w_dd[a])


def test_deadband_mapping():
    cfg = v3_config()
    cfg.deadband = 0.3
    row = {a: {"tsm": 0.0, "mr": 0.0, "xs": s, "vol_ann": 0.5}
           for a, s in zip(cfg.assets, (0.1, -0.2, 0.65, 1.0))}
    w = target_weights(row, 0.0, cfg)
    assert w["BTC"] == 0.0 and w["ETH"] == 0.0       # inside the deadband
    assert w["SOL"] > 0.0                             # rescaled, still long
    assert abs(w["HYPE"]) >= abs(w["SOL"])            # ordering preserved
