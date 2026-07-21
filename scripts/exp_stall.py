"""X-A stall-tightened chandelier — pre-registered measurement (EXIT_REDESIGN.md).

Design window 2023-06-01~2026-01-31, whale profile, half-cross (H1/H2 split at the
temporal midpoint 2024-09-30 12:00). stall params only touch engine exit logic, so
prepare() is invariant to them — we prepare once and re-run the engine per cell.

Gate (G5): terminal wealth / MDD / avg_r not degraded vs whale baseline on the full
window, AND sign stability across the sweep + both halves (no cell/half sign flip).

Usage: python3 scripts/exp_stall.py
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
H1 = (FULL[0], MID)
H2 = (MID, FULL[1])

STALL_BARS = [18, 24, 36]
STALL_TRAIL = [2.5, 3.0, 4.0]

BASE = profile("whale")  # baseline: stall_bars=0 (X-A off)


def run(prep, p, window):
    frames, features, candidates, funding, states, corr = prep
    res = engine_run(frames, features, candidates, funding, states, corr, p,
                     start=window[0], end=window[1], equity0=10_000.0)
    return metrics(res["trades"], res["equity"], 10_000.0)


def row(prep, p):
    """full metrics + H1/H2 avg_r for the half-cross sign check."""
    mf = run(prep, p, FULL)
    m1 = run(prep, p, H1)
    m2 = run(prep, p, H2)
    return {
        "ret": mf.get("total_return"), "mdd": mf.get("max_dd"),
        "avg_r": mf.get("avg_r"), "n": mf.get("trades"),
        "geo_m": mf.get("monthly_geo"),
        "h1_r": m1.get("avg_r"), "h2_r": m2.get("avg_r"),
        "h1_ret": m1.get("total_return"), "h2_ret": m2.get("total_return"),
    }


def main():
    frames, funding = load_universe(DATA)
    features, candidates, states, corr = prepare(frames, funding, BASE)
    prep = (frames, features, candidates, funding, states, corr)

    print("=== BASELINE whale (stall OFF) ===")
    base = row(prep, BASE)
    fmt = ("ret={ret} geo_m={geo_m} mdd={mdd} avg_r={avg_r} n={n} | "
           "H1 r={h1_r} ret={h1_ret} | H2 r={h2_r} ret={h2_ret}")
    print(fmt.format(**base))

    print("\n=== X-A SWEEP (stall_bars x stall_trail_atr, peak_r=0.5) ===")
    print(f"{'bars':>5} {'trail':>6} | {'ret':>9} {'geo_m':>7} {'mdd':>8} "
          f"{'avg_r':>7} {'n':>4} | {'H1_r':>7} {'H2_r':>7} {'H1_ret':>9} {'H2_ret':>9}")
    for sb in STALL_BARS:
        for st in STALL_TRAIL:
            p = replace(BASE, stall_bars=sb, stall_trail_atr=st, stall_peak_r=0.5)
            r = row(prep, p)
            print(f"{sb:>5} {st:>6} | {r['ret']:>9} {r['geo_m']:>7} {r['mdd']:>8} "
                  f"{r['avg_r']:>7} {r['n']:>4} | {r['h1_r']:>7} {r['h2_r']:>7} "
                  f"{r['h1_ret']:>9} {r['h2_ret']:>9}")

    print("\nbaseline for reference:")
    print(fmt.format(**base))


if __name__ == "__main__":
    main()
