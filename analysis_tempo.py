"""1H vs 4H tempo decision, re-analyzed with maker-fee order economics.

Discipline against curve-fitting:
  - variant SELECTION uses only the 2025 design window;
  - 2026 acts as an untouched holdout, reported for the chosen variant;
  - the only degrees of freedom are order type (maker/taker) and bar size —
    no signal parameter is touched.

Variants (P1 breakout entries always stay stop-market/taker):
  1H-taker | 1H-mExit | 1H-mExit+optimistic | 1H-mExit+realistic
  4H-taker | 4H-mExit | 4H-mExit+realistic

Output: ANALYSIS_TEMPO.md
"""
import copy

import pandas as pd

from fabletradebot import Backtester, Config
from fabletradebot.config import h4_config
from fabletradebot.data_okx import load_market
from fabletradebot.preprocess import resample_ohlcv
from validation import (gate1_walkforward, gate2_sensitivity, gate3_cost_stress,
                        gate4_monte_carlo, EQUITY0)

DESIGN = ("2025-01-01", "2025-12-31")
HOLDOUT = ("2026-01-01", "2026-07-08")


def variant(base_fn, maker_exits=False, maker_entries="none", label=""):
    cfg = base_fn()
    cfg.maker_exits = maker_exits
    cfg.maker_entries = maker_entries
    return label, cfg


VARIANTS = [
    variant(Config, False, "none", "1H-taker"),
    variant(Config, True, "none", "1H-mExit"),
    variant(Config, True, "optimistic", "1H-mExit+opt"),
    variant(Config, True, "realistic", "1H-mExit+real"),
    variant(h4_config, False, "none", "4H-taker"),
    variant(h4_config, True, "none", "4H-mExit"),
    variant(h4_config, True, "realistic", "4H-mExit+real"),
]


def window_stats(res, t0: str, t1: str) -> dict:
    eq = res.equity.loc[t0:t1]
    tr = res.trades
    if len(tr):
        ts = pd.to_datetime(tr["closed_ts"])
        tr = tr[(ts >= pd.Timestamp(t0, tz="UTC")) & (ts <= pd.Timestamp(t1, tz="UTC") + pd.Timedelta(days=1))]
    if len(eq) < 2:
        return dict(ret=0.0, mdd=0.0, n=0, avg_r=0.0, sum_r=0.0)
    return dict(
        ret=eq.iloc[-1] / eq.iloc[0] - 1.0,
        mdd=(eq / eq.cummax() - 1.0).min(),
        n=len(tr),
        avg_r=tr["r"].mean() if len(tr) else 0.0,
        sum_r=tr["r"].sum() if len(tr) else 0.0,
    )


def main():
    data1h, funding = load_market("2025-01-01", "2026-07-08")
    data4h = {a: resample_ohlcv(df) for a, df in data1h.items()}

    rows = []
    results = {}
    for label, cfg in VARIANTS:
        data = data4h if label.startswith("4H") else data1h
        res = Backtester(data, cfg, funding=funding, equity0=EQUITY0).run()
        results[label] = (cfg, res)
        d = window_stats(res, *DESIGN)
        h = window_stats(res, *HOLDOUT)
        rows.append(dict(label=label, design=d, holdout=h,
                         fees=res.stats["fees"], full=res.stats["total_return"]))
        print(f"{label:16s} design: {d['ret']:+7.2%} ({d['n']:3d} tr, avgR {d['avg_r']:+.3f}) "
              f"| holdout: {h['ret']:+7.2%} ({h['n']:3d} tr) | fees {res.stats['fees']:8.0f}")

    # ---- selection: design window only, must not breach -15% there ----
    eligible = [r for r in rows if r["design"]["mdd"] > -0.15]
    winner = max(eligible, key=lambda r: r["design"]["ret"])
    w_label = winner["label"]
    w_cfg, w_res = results[w_label]
    print(f"\nselected on design window: {w_label}")

    # ---- gates + risk frontier for the winner (full period) ----
    data = data4h if w_label.startswith("4H") else data1h
    g1 = gate1_walkforward(data, funding, w_cfg)
    g2 = gate2_sensitivity(data, funding, w_cfg)
    g3 = gate3_cost_stress(data, funding, w_cfg)
    g4 = gate4_monte_carlo(g1["res"].trades)
    gates_pass = all([g1["passed"], g2["passed"], g3["passed"], g4["passed"]])

    frontier = []
    for rb in (0.0075, 0.0125, 0.02):
        cfg = copy.deepcopy(w_cfg)
        cfg.r_base = rb
        res = Backtester(data, cfg, funding=funding, equity0=EQUITY0).run()
        frontier.append((rb, res.stats["total_return"], res.stats["max_dd"]))
        print(f"r_base {rb:.2%}: return {res.stats['total_return']:+.2%}, "
              f"MDD {res.stats['max_dd']:+.2%}")

    # ---- report ----
    L = ["# ANALYSIS — 1H vs 4H tempo, maker-economics re-run", ""]
    L.append(f"Design window {DESIGN[0]}..{DESIGN[1]} (selection), "
             f"holdout {HOLDOUT[0]}..{HOLDOUT[1]} (untouched until selection was made).")
    L.append("Only order type and bar size vary; no signal parameter was re-tuned.")
    L.append("")
    L.append("| variant | design ret | design MDD | design trades | design avgR "
             "| holdout ret | holdout trades | fees (full) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        d, h = r["design"], r["holdout"]
        L.append(f"| {r['label']} | {d['ret']:+.2%} | {d['mdd']:+.2%} | {d['n']} "
                 f"| {d['avg_r']:+.3f} | {h['ret']:+.2%} | {h['n']} | {r['fees']:.0f} |")
    L.append("")
    L.append(f"## Winner on design window: **{w_label}**")
    hs = winner["holdout"]
    L.append(f"- holdout (2026, untouched): {hs['ret']:+.2%}, {hs['n']} trades, "
             f"avg R {hs['avg_r']:+.3f}")
    L.append(f"- full-period gates: "
             f"G1 {'PASS' if g1['passed'] else 'FAIL'} "
             f"({g1['res'].stats['total_return']:+.2%}, MDD {g1['res'].stats['max_dd']:+.2%}), "
             f"G2 {'PASS' if g2['passed'] else 'FAIL'}, "
             f"G3 {'PASS' if g3['passed'] else 'FAIL'} "
             f"({g3['res'].stats['total_return']:+.2%}), "
             f"G4 {'PASS' if g4['passed'] else 'FAIL'} (95th pct MDD {g4['p95']:+.2%})")
    L.append("")
    L.append("## Risk-scaling frontier (winner, full period)")
    L.append("| r_base | return | max DD |")
    L.append("|---|---|---|")
    for rb, ret, mdd in frontier:
        L.append(f"| {rb:.2%} | {ret:+.2%} | {mdd:+.2%} |")
    L.append("")
    L.append(f"## FINAL: {w_label} — gates {'ALL PASS' if gates_pass else 'FAILED'}")
    with open("ANALYSIS_TEMPO.md", "w") as f:
        f.write("\n".join(L) + "\n")
    print("\nwrote ANALYSIS_TEMPO.md")


if __name__ == "__main__":
    main()
