"""Validation gates G4-G7: walk-forward, ±20% sensitivity, cost stress, Monte Carlo.

All of these consume the same deterministic backtest; MC uses a seeded RNG.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .config import Params


def walk_forward(data_dir: str, p: Params, start: str, end: str,
                 test_months: int = 2) -> pd.DataFrame:
    """Rolling out-of-sample slices (parameters are FIXED — this checks stability
    across periods, not re-fitting)."""
    edges = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                          freq=f"{test_months}MS")
    rows = []
    for i in range(len(edges) - 1):
        res = run_backtest(data_dir, p, start=str(edges[i].date()),
                           end=str(edges[i + 1].date()))
        m = res["metrics"]
        rows.append({"from": edges[i].date(), "to": edges[i + 1].date(),
                     "trades": m.get("trades", 0), "avg_r": m.get("avg_r"),
                     "total_return": m.get("total_return"), "max_dd": m.get("max_dd")})
    return pd.DataFrame(rows)


SENSITIVITY_KNOBS = ("conf_entry", "sl_floor_atr", "sl_swing_atr", "trail_atr",
                     "brk_vol_mult", "brk_lookback", "cooldown_bars", "funding_z_ext")


def sensitivity(data_dir: str, p: Params, start: str, end: str,
                rel: float = 0.20) -> pd.DataFrame:
    rows = []
    base = run_backtest(data_dir, p, start=start, end=end)["metrics"]
    rows.append({"knob": "BASE", "delta": 0, "avg_r": base.get("avg_r"),
                 "trades": base.get("trades"), "total_return": base.get("total_return")})
    for knob in SENSITIVITY_KNOBS:
        for sign in (-1, 1):
            v0 = getattr(p, knob)
            v = type(v0)(v0 * (1 + sign * rel))
            m = run_backtest(data_dir, replace(p, **{knob: v}),
                             start=start, end=end)["metrics"]
            rows.append({"knob": knob, "delta": sign * rel, "value": v,
                         "avg_r": m.get("avg_r"), "trades": m.get("trades"),
                         "total_return": m.get("total_return")})
    return pd.DataFrame(rows)


def cost_stress(data_dir: str, p: Params, start: str, end: str,
                mult: float = 2.0) -> dict:
    return run_backtest(data_dir, replace(p, cost_mult=mult),
                        start=start, end=end)["metrics"]


def monte_carlo(trades: pd.DataFrame, equity0: float = 10_000.0,
                n_paths: int = 1000, block: int = 10, seed: int = 42) -> dict:
    """Block-bootstrap the realized per-trade pnl fractions and measure MDD tails.

    Uses pnl as a fraction of equity at trade time so paths compound honestly.
    """
    if len(trades) < 20:
        return {"error": "too few trades for MC"}
    eq_before = trades["equity_after"] - trades["pnl"]
    frac = (trades["pnl"] / eq_before).to_numpy()
    rng = np.random.default_rng(seed)
    n = len(frac)
    mdds, finals = [], []
    for _ in range(n_paths):
        picks = []
        while len(picks) < n:
            s = rng.integers(0, n)
            picks.extend(frac[s:s + block])
        path = np.cumprod(1 + np.array(picks[:n]))
        peak = np.maximum.accumulate(np.concatenate([[1.0], path]))
        mdd = ((np.concatenate([[1.0], path]) / peak) - 1).min()
        mdds.append(mdd)
        finals.append(path[-1])
    mdds = np.array(mdds)
    return {
        "paths": n_paths,
        "mdd_p50": round(float(np.percentile(mdds, 50)), 4),
        "mdd_p95": round(float(np.percentile(-mdds, 95)) * -1, 4),
        "p_mdd_gt_30": round(float((mdds < -0.30).mean()), 4),
        "p_mdd_gt_50": round(float((mdds < -0.50).mean()), 4),
        "final_p5": round(float(np.percentile(finals, 5)), 3),
        "final_p50": round(float(np.percentile(finals, 50)), 3),
        "final_p95": round(float(np.percentile(finals, 95)), 3),
    }
