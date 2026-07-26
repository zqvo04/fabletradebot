"""Z-A high-armed fractional give-back floor — pre-registered measurement
(GIVEBACK_REDESIGN.md §10).

Design window 2023-06-01~2026-01-31, whale profile, half-cross (H1/H2 split at the
temporal midpoint 2024-09-30 12:00). Z-A only touches engine exit logic, so
prepare() is invariant to it — prepare once, re-run the engine per cell.

PRIMARY CELL is arm=8R, giveback=0.50, fixed a priori from the Q1 breathing
audit BEFORE any outcome was seen: the deepest retracement any design-window
trade SURVIVED once its running peak had reached 8R was 40% of that peak, so a
50% floor clears the runners' envelope by 25%. The grid is a flatness check —
adopting the argmax is forbidden.

Gate (G5): terminal wealth / MDD / avg_r not degraded vs the whale baseline on
the full window AND on both halves; sign stability across the grid.

Usage: python3 scripts/exp_giveback_arm.py
"""
import sys
from dataclasses import replace

sys.path.insert(0, ".")

import pandas as pd

from fabletradebot.backtest import load_universe, metrics, prepare
from fabletradebot.config import profile
from fabletradebot.engine import run as engine_run

DATA = "data"
FULL = (pd.Timestamp("2023-06-01", tz="UTC"), pd.Timestamp("2026-01-31", tz="UTC"))
MID = pd.Timestamp("2024-09-30 12:00", tz="UTC")

ARM = [6.0, 8.0, 10.0]
GIVEBACK = [0.4, 0.5, 0.6]

BASE = profile("whale")   # baseline: hold_giveback=1.0 (leg disabled, E16)


def row(prep, p):
    frames, features, candidates, funding, states, corr = prep

    def m(window):
        res = engine_run(frames, features, candidates, funding, states, corr, p,
                         start=window[0], end=window[1], equity0=10_000.0)
        return metrics(res["trades"], res["equity"], 10_000.0)

    mf, m1, m2 = m(FULL), m((FULL[0], MID)), m((MID, FULL[1]))
    return {"ret": mf.get("total_return"), "mdd": mf.get("max_dd"),
            "avg_r": mf.get("avg_r"), "pf": mf.get("profit_factor"),
            "n": mf.get("trades"),
            "h1_r": m1.get("avg_r"), "h1_ret": m1.get("total_return"),
            "h2_r": m2.get("avg_r"), "h2_ret": m2.get("total_return")}


HDR = (f"{'cell':>12} | {'ret':>9} {'mdd':>8} {'avg_r':>7} {'pf':>6} {'n':>4} | "
       f"{'H1_r':>7} {'H1_ret':>8} | {'H2_r':>7} {'H2_ret':>8}")


def show(label, r):
    print(f"{label:>12} | {r['ret']:>9} {r['mdd']:>8} {r['avg_r']:>7} {r['pf']:>6} "
          f"{r['n']:>4} | {r['h1_r']:>7} {r['h1_ret']:>8} | "
          f"{r['h2_r']:>7} {r['h2_ret']:>8}")


def main():
    frames, funding = load_universe(DATA)
    features, candidates, states, corr = prepare(frames, funding, BASE)
    prep = (frames, features, candidates, funding, states, corr)

    print("=== Z-A grid: give-back floor armed at a HIGH peak (whale, design window) ===")
    print(HDR)
    show("OFF", row(prep, BASE))
    for arm in ARM:
        for gb in GIVEBACK:
            p = replace(BASE, hold_giveback=gb, hold_giveback_arm=arm)
            show(f"arm{arm:g}/gb{gb}", row(prep, p))

    print("\n=== E16 control: the SAME object armed low (why it was rejected) ===")
    print(HDR)
    for arm in (1.0, 2.0):
        show(f"arm{arm:g}/gb0.5",
             row(prep, replace(BASE, hold_giveback=0.5, hold_giveback_arm=arm)))

    print("\n=== primary cell arm=8/gb=0.5 — cost stress (G6) ===")
    print(HDR)
    show("OFF x2c", row(prep, replace(BASE, cost_mult=2.0)))
    show("8/0.5 x2c", row(prep, replace(BASE, hold_giveback=0.5,
                                        hold_giveback_arm=8.0, cost_mult=2.0)))


if __name__ == "__main__":
    main()
