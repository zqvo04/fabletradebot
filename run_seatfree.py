"""Stage-1 judging harness: python3 run_seatfree.py [start] [end] [data_dir] [profile]

The seat-free record is where slot and exit parameters are DECIDED. The whale
backtest is not: with one compounding full-margin seat its terminal return is
path-dominated (reshuffling the same 451 design-window trades spans p5 1.81x to
p95 206.84x), so config comparisons on it read sequence noise. Lifting the seat
gives ~3x the trades over the same bars and, crucially, an expectancy whose
interval is not swamped by which trade happened to land while equity was high.

Read the week-block CI, not the naive one: 60% of these trades open before the
previous one closed, so the real sample size is the week count, not the row
count. See validation.week_block_ci.

Judging rule (pre-registered, REGIME_REDESIGN.md): a slot or variant is ADOPTED
only when its week-block CI excludes zero AND both chronological halves share
the mean's sign AND n >= 150. Everything else is "cannot tell" -- which is the
honest verdict for most of this table.
"""
import sys

import pandas as pd

from fabletradebot import shadow
from fabletradebot.backtest import load_universe, prepare
from fabletradebot.config import profile
from fabletradebot.engine import run as engine_run
from fabletradebot.validation import week_block_ci

MIN_N = 150


def run_seatfree(data_dir: str, p, start: str, end: str, equity0: float = 10_000.0):
    sf = shadow.params_backtest(p)
    frames, funding = load_universe(data_dir)
    features, candidates, states, corr = prepare(frames, funding, sf)
    return engine_run(frames, features, candidates, funding, states, corr, sf,
                      start=pd.Timestamp(start, tz="UTC"),
                      end=pd.Timestamp(end, tz="UTC"), equity0=equity0)


def report(trades: pd.DataFrame, by: str = "setup") -> str:
    t = trades.sort_values("opened")
    lines = []

    def row(label, g):
        lo, hi, k = week_block_ci(g)
        mid = g["opened"].quantile(0.5)
        h1, h2 = g[g.opened <= mid]["r"].mean(), g[g.opened > mid]["r"].mean()
        same = (h1 > 0) == (h2 > 0)
        decided = (not pd.isna(lo)) and (lo > 0 or hi < 0) and same and len(g) >= MIN_N
        verdict = "-" if not decided else ("EDGE+" if lo > 0 else "EDGE-")
        return (f"  {label:<10} n={len(g):>5} wk={k:>4} mean={g['r'].mean():>+8.4f} "
                f"CI=[{lo:>+8.4f},{hi:>+8.4f}] h1={h1:>+7.3f} h2={h2:>+7.3f} "
                f"{'SAME' if same else 'FLIP'}  {verdict}")

    lines.append(f"== seat-free, judged on week blocks (floor n>={MIN_N}) ==")
    lines.append(row("ALL", t))
    lines.append(f"\n== by {by} ==")
    for k, g in t.groupby(by):
        lines.append(row(str(k), g))
    lines.append("\n  EDGE+/EDGE- = CI excludes 0 AND both halves same sign AND n>=floor.")
    lines.append("  '-' is 'cannot tell', not 'no edge'.")
    return "\n".join(lines)


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2023-06-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-01-31"
    data_dir = sys.argv[3] if len(sys.argv) > 3 else "data"
    prof = sys.argv[4] if len(sys.argv) > 4 else "whale"
    res = run_seatfree(data_dir, profile(prof), start, end)
    print(report(res["trades"]))
