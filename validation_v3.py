"""BLUEPRINT §6 validation gates adapted to the v3 continuous portfolio
system (no discrete trades -> Monte Carlo runs on daily-return blocks).

Gate 1  Walk-forward: full-period run + quarterly segments.
Gate 2  Sensitivity: xs_look / vol_budget / band perturbed +-20% (8 corners)
        -> no corner may lose more than 25% (return or MDD).
Gate 3  Cost stress: fees x2 and slippage x2 -> total return must stay positive.
Gate 4  Monte Carlo: stationary bootstrap of daily returns (2000 runs, mean
        block 5 days) -> 95th percentile max drawdown within -25%.

Usage: python3 validation_v3.py [start] [end] [profile]
       profile: v3 (default) | v4 (aggressive risk profile)
                | v5 (maker execution + satellite universe)
"""
import copy
import sys

import numpy as np
import pandas as pd

from fabletradebot.v3 import V3Backtester, v3_config, v4_config, v5_config
from fabletradebot.data_okx import INSTRUMENTS, load_market
from fabletradebot.preprocess import resample_ohlcv

MC_RUNS = 2000
MC_MDD_LIMIT = -0.25
MC_BLOCK_DAYS = 5
EQUITY0 = 100_000.0


def run_bt(data, funding, cfg):
    return V3Backtester(data, cfg, funding=funding, equity0=EQUITY0).run()


def fmt_pct(x):
    return f"{x:+.2%}"


def gate1_walkforward(data, funding, cfg):
    res = run_bt(data, funding, cfg)
    eq = res.equity
    quarters = []
    for q, seg in eq.groupby(pd.PeriodIndex(eq.index, freq="Q")):
        quarters.append((str(q), seg.iloc[-1] / seg.iloc[0] - 1.0,
                         (seg / seg.cummax() - 1.0).min()))
    passed = res.stats["total_return"] > 0 and res.stats["max_dd"] > -0.25
    return dict(res=res, quarters=quarters, passed=passed)


def _perturbed_cfgs(base):
    out = []
    for x_m in (0.8, 1.2):
        for v_m in (0.8, 1.2):
            for b_m in (0.8, 1.2):
                cfg = copy.deepcopy(base)
                cfg.xs_look = int(round(base.xs_look * x_m))
                cfg.vol_budget = base.vol_budget * v_m
                cfg.band = base.band * b_m
                out.append((f"xs_look x{x_m} | vol_budget x{v_m} | band x{b_m}", cfg))
    return out


def gate2_sensitivity(data, funding, base_cfg):
    rows = []
    for label, cfg in _perturbed_cfgs(base_cfg):
        res = run_bt(data, funding, cfg)
        rows.append(dict(label=label, total=res.stats["total_return"],
                         mdd=res.stats["max_dd"], sharpe=res.stats["sharpe"]))
    passed = all(r["mdd"] > -0.25 and r["total"] > -0.25 for r in rows)
    return dict(rows=rows, passed=passed)


def gate3_cost_stress(data, funding, base_cfg):
    cfg = copy.deepcopy(base_cfg)
    cfg.fee_bps = base_cfg.fee_bps * 2
    cfg.maker_fee_bps = base_cfg.maker_fee_bps * 2   # v5 maker path too
    cfg.slip_bps = {k: v * 2 for k, v in base_cfg.slip_bps.items()}
    cfg.sat_slip_bps = base_cfg.sat_slip_bps * 2
    res = run_bt(data, funding, cfg)
    passed = res.stats["total_return"] > 0
    return dict(res=res, passed=passed)


def gate4_monte_carlo(equity: pd.Series, seed: int = 0):
    """Stationary bootstrap (Politis-Romano) of daily returns."""
    daily = equity.resample("1D").last().dropna().pct_change().dropna().to_numpy()
    n = len(daily)
    if n < 30:
        return dict(passed=False, mdds=np.array([]), p95=0.0)
    rng = np.random.default_rng(seed)
    p = 1.0 / MC_BLOCK_DAYS
    mdds = np.empty(MC_RUNS)
    for k in range(MC_RUNS):
        idx = np.empty(n, dtype=int)
        j = rng.integers(n)
        for t in range(n):
            idx[t] = j
            j = rng.integers(n) if rng.random() < p else (j + 1) % n
        eq = np.cumprod(1.0 + daily[idx])
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
        mdds[k] = (np.concatenate([[1.0], eq]) / peak - 1.0).min()
    p95 = float(np.percentile(mdds, 5))
    return dict(passed=p95 >= MC_MDD_LIMIT, mdds=mdds, p95=p95)


def main(start="2025-01-01", end="2026-07-08", profile="v3"):
    profile = profile.lower()
    cfg = {"v4": v4_config, "v5": v5_config}.get(profile, v3_config)()
    assets = dict(INSTRUMENTS)
    assets.update({a: f"{a}-USDT-SWAP" for a in cfg.satellites})
    data, funding = load_market(start, end, assets=assets)
    # satellite caches reach back to their 2024 listings — clamp every asset
    # to the requested window so the run matches the design-window studies
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    data = {a: resample_ohlcv(df.loc[(df.index >= lo) & (df.index <= hi)])
            for a, df in data.items()}
    print(f"assets loaded (4H, {profile}): { {a: len(df) for a, df in data.items()} }")

    g1 = gate1_walkforward(data, funding, cfg)
    g2 = gate2_sensitivity(data, funding, cfg)
    g3 = gate3_cost_stress(data, funding, cfg)
    g4 = gate4_monte_carlo(g1["res"].equity)

    s = g1["res"].stats
    lines = [f"# VALIDATION ({profile.upper()}) — §6 gates adapted to the continuous portfolio system", ""]
    lines.append(f"Period: {start} .. {end} (4H bars, params fixed at frozen v3 values, "
                 f"equity0 = {EQUITY0:,.0f})")
    lines.append(f"Assets: {', '.join(s['assets'])}")
    lines.append("")

    lines.append(f"## Gate 1 — Walk-forward (fixed params): "
                 f"{'PASS' if g1['passed'] else 'FAIL'}")
    lines.append(f"- total return {fmt_pct(s['total_return'])}, "
                 f"max DD {fmt_pct(s['max_dd'])}, ann vol {s['ann_vol']:.1%}, "
                 f"sharpe {s['sharpe']:.2f}, turnover {s['turnover_yr']:.1f}x/yr, "
                 f"fees {s['fees']:,.0f}, net funding {s['funding']:,.0f}")
    lines.append("- quarterly segments (return / max DD):")
    for q, r, dd in g1["quarters"]:
        lines.append(f"  - {q}: {fmt_pct(r)} / {fmt_pct(dd)}")
    lines.append("")

    lines.append(f"## Gate 2 — Sensitivity +-20% (8 corners): "
                 f"{'PASS' if g2['passed'] else 'FAIL'}")
    for r in g2["rows"]:
        lines.append(f"- {r['label']}: return {fmt_pct(r['total'])}, "
                     f"MDD {fmt_pct(r['mdd'])}, sharpe {r['sharpe']:.2f}")
    lines.append("")

    s3 = g3["res"].stats
    lines.append(f"## Gate 3 — Cost stress (fees x2, slippage x2): "
                 f"{'PASS' if g3['passed'] else 'FAIL'}")
    lines.append(f"- total return {fmt_pct(s3['total_return'])}, "
                 f"sharpe {s3['sharpe']:.2f}, fees {s3['fees']:,.0f}")
    lines.append("")

    lines.append(f"## Gate 4 — Monte Carlo stationary bootstrap ({MC_RUNS} runs, "
                 f"~{MC_BLOCK_DAYS}d blocks): {'PASS' if g4['passed'] else 'FAIL'}")
    if len(g4["mdds"]):
        lines.append(f"- MDD distribution: median {fmt_pct(float(np.median(g4['mdds'])))}, "
                     f"95th pct {fmt_pct(g4['p95'])} (limit {fmt_pct(MC_MDD_LIMIT)})")
    lines.append("")

    verdict = all([g1["passed"], g2["passed"], g3["passed"], g4["passed"]])
    lines.append(f"## VERDICT: {'ALL GATES PASS' if verdict else 'GATES FAILED — do not deploy'}")
    report = "\n".join(lines)
    print(report)
    with open(f"VALIDATION_{profile.upper()}.md", "w") as f:
        f.write(report + "\n")
    return verdict


if __name__ == "__main__":
    main(*sys.argv[1:4])
