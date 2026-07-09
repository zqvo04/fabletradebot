"""BLUEPRINT §6 validation gates, run on real OKX data with FIXED parameters
(no in-sample optimization).

Gate 1  Walk-forward: full-period backtest + quarterly segment breakdown.
Gate 2  Sensitivity: theta / ER threshold / ATR multiples perturbed +-20%
        -> system must survive every corner (no ruin).
Gate 3  Cost stress: fees x2 and slippage x2 -> expectancy must stay positive.
Gate 4  Monte Carlo: shuffle (risk_frac, R) trade sequence 2000x
        -> 95th percentile max drawdown must be within -25%.

Usage: python3 validation.py [start] [end] [timeframe]
       (defaults 2025-01-01 .. 2026-07-08 1H; pass 4H for the swing-tempo
       variant, V2 for the v2 redesign on 4H bars)
"""
import copy
import sys

import numpy as np
import pandas as pd

from fabletradebot import Backtester, Config
from fabletradebot.config import h4_config, v2_config
from fabletradebot.data_okx import load_market
from fabletradebot.preprocess import resample_ohlcv

MC_RUNS = 2000
MC_MDD_LIMIT = -0.25
EQUITY0 = 100_000.0


def run_bt(data, funding, cfg):
    return Backtester(data, cfg, funding=funding, equity0=EQUITY0).run()


def fmt_pct(x):
    return f"{x:+.2%}"


# ---------------- Gate 1: walk-forward ----------------

def gate1_walkforward(data, funding, cfg):
    res = run_bt(data, funding, cfg)
    eq = res.equity
    quarters = []
    for q, seg in eq.groupby(pd.PeriodIndex(eq.index, freq="Q")):
        r = seg.iloc[-1] / seg.iloc[0] - 1.0
        dd = (seg / seg.cummax() - 1.0).min()
        quarters.append((str(q), r, dd))
    # exclude warmup-only leading quarters (flat equity)
    active = [(q, r, dd) for q, r, dd in quarters if abs(r) > 1e-12 or dd < -1e-12]
    passed = res.stats["total_return"] > 0 and res.stats["max_dd"] > -0.25
    return dict(res=res, quarters=quarters, active=active, passed=passed)


# ---------------- Gate 2: parameter sensitivity ----------------

def _perturbed_cfgs(base: Config):
    """8 corners of {theta, er_trend, ATR multiples} x {0.8, 1.2} + base."""
    out = []
    for t_m in (0.8, 1.2):
        for e_m in (0.8, 1.2):
            for a_m in (0.8, 1.2):
                cfg = copy.deepcopy(base)
                cfg.theta = {k: min(v * t_m, 0.99) for k, v in base.theta.items()}
                cfg.er_trend = base.er_trend * e_m
                cfg.chandelier_atr = base.chandelier_atr * a_m
                cfg.pyr_advance_atr = base.pyr_advance_atr * a_m
                cfg.min_stop_atr = {k: v * a_m for k, v in base.min_stop_atr.items()}
                out.append((f"theta x{t_m} | ER x{e_m} | ATR x{a_m}", cfg))
    return out


def gate2_sensitivity(data, funding, base_cfg):
    rows = []
    for label, cfg in _perturbed_cfgs(base_cfg):
        res = run_bt(data, funding, cfg)
        rows.append(dict(label=label, total=res.stats["total_return"],
                         mdd=res.stats["max_dd"], n=res.stats["n_trades"]))
    # "no ruin": every corner keeps equity above the -25% line on close marks
    passed = all(r["mdd"] > -0.25 and r["total"] > -0.25 for r in rows)
    return dict(rows=rows, passed=passed)


# ---------------- Gate 3: cost stress ----------------

def gate3_cost_stress(data, funding, base_cfg):
    cfg = copy.deepcopy(base_cfg)
    cfg.fee_bps = base_cfg.fee_bps * 2
    cfg.slip_bps = {k: v * 2 for k, v in base_cfg.slip_bps.items()}
    res = run_bt(data, funding, cfg)
    passed = res.stats["avg_r"] > 0 and res.stats["total_return"] > 0
    return dict(res=res, passed=passed)


# ---------------- Gate 4: Monte Carlo sequence shuffle ----------------

def gate4_monte_carlo(trades: pd.DataFrame, seed: int = 0):
    if len(trades) == 0:
        return dict(passed=False, mdds=np.array([]), p95=0.0)
    rng = np.random.default_rng(seed)
    pairs = trades[["risk_frac", "r"]].to_numpy()
    mdds = np.empty(MC_RUNS)
    for k in range(MC_RUNS):
        idx = rng.permutation(len(pairs))
        eq = np.cumprod(1.0 + pairs[idx, 0] * pairs[idx, 1])
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
        mdds[k] = (np.concatenate([[1.0], eq]) / peak - 1.0).min()
    p95 = float(np.percentile(mdds, 5))  # 95th worst percentile of MDD
    return dict(passed=p95 >= MC_MDD_LIMIT, mdds=mdds, p95=p95)


# ---------------- report ----------------

def main(start="2025-01-01", end="2026-07-08", timeframe="1H"):
    data, funding = load_market(start, end)
    if timeframe.upper() == "4H":
        data = {a: resample_ohlcv(df) for a, df in data.items()}
        cfg = h4_config()
    elif timeframe.upper() == "V2":
        data = {a: resample_ohlcv(df) for a, df in data.items()}
        cfg = v2_config()
    else:
        cfg = Config()
    print(f"assets loaded ({timeframe}): { {a: len(df) for a, df in data.items()} }")

    g1 = gate1_walkforward(data, funding, cfg)
    g2 = gate2_sensitivity(data, funding, cfg)
    g3 = gate3_cost_stress(data, funding, cfg)
    g4 = gate4_monte_carlo(g1["res"].trades)

    s = g1["res"].stats
    lines = [f"# VALIDATION ({timeframe}) — BLUEPRINT §6 gates on real OKX data", ""]
    lines.append(f"Period: {start} .. {end} ({timeframe} bars, params fixed at spec values, "
                 f"equity0 = {EQUITY0:,.0f})")
    lines.append(f"Assets: {', '.join(s['assets'])}")
    lines.append("")

    lines.append(f"## Gate 1 — Walk-forward (fixed params): "
                 f"{'PASS' if g1['passed'] else 'FAIL'}")
    lines.append(f"- total return {fmt_pct(s['total_return'])}, "
                 f"max DD {fmt_pct(s['max_dd'])}, trades {s['n_trades']}, "
                 f"win rate {s['win_rate']:.1%}, avg R {s['avg_r']:+.3f}, "
                 f"profit factor {s['profit_factor']:.2f}")
    lines.append("- quarterly segments (return / max DD):")
    for q, r, dd in g1["quarters"]:
        lines.append(f"  - {q}: {fmt_pct(r)} / {fmt_pct(dd)}")
    lines.append("- by playbook:")
    for pb, row in s["by_playbook"].items():
        lines.append(f"  - {pb}: n={row['n']}, win={row['win']:.0%}, "
                     f"avgR={row['avg_r']:+.2f}, sumR={row['sum_r']:+.1f}")
    lines.append("")

    lines.append(f"## Gate 2 — Sensitivity +-20% (8 corners): "
                 f"{'PASS' if g2['passed'] else 'FAIL'}")
    for r in g2["rows"]:
        lines.append(f"- {r['label']}: return {fmt_pct(r['total'])}, "
                     f"MDD {fmt_pct(r['mdd'])}, trades {r['n']}")
    lines.append("")

    s3 = g3["res"].stats
    lines.append(f"## Gate 3 — Cost stress (fees x2, slippage x2): "
                 f"{'PASS' if g3['passed'] else 'FAIL'}")
    lines.append(f"- total return {fmt_pct(s3['total_return'])}, avg R {s3['avg_r']:+.3f}, "
                 f"trades {s3['n_trades']}, profit factor {s3['profit_factor']:.2f}")
    lines.append("")

    lines.append(f"## Gate 4 — Monte Carlo sequence shuffle ({MC_RUNS} runs): "
                 f"{'PASS' if g4['passed'] else 'FAIL'}")
    if len(g4["mdds"]):
        lines.append(f"- MDD distribution: median {fmt_pct(float(np.median(g4['mdds'])))}, "
                     f"95th pct {fmt_pct(g4['p95'])} (limit {fmt_pct(MC_MDD_LIMIT)})")
    lines.append("")

    verdict = all([g1["passed"], g2["passed"], g3["passed"], g4["passed"]])
    lines.append(f"## VERDICT: {'ALL GATES PASS' if verdict else 'GATES FAILED — do not deploy'}")
    report = "\n".join(lines)
    print(report)
    fname = "VALIDATION.md" if timeframe.upper() == "1H" else f"VALIDATION_{timeframe.upper()}.md"
    with open(fname, "w") as f:
        f.write(report + "\n")
    return verdict


if __name__ == "__main__":
    main(*sys.argv[1:4])
