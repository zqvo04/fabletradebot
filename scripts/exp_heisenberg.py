"""E21 H axis (Heisenberg release) — pre-registered measurement.

HEISENBERG_REDESIGN.md. Design window 2023-06-01~2026-01-31, whale profile,
half-cross (H1/H2 split at the temporal midpoint 2024-09-30 12:00).

H-A/H-B/H-C touch engine exit logic only and H-D touches the entry gate inside
the engine, so prepare() is invariant to every cell — prepare once, re-run the
engine per cell (same protocol as scripts/exp_stall.py).

Gate (G5): terminal wealth / MDD / avg_r not degraded vs whale baseline on the
full window, AND sign stability across the sweep and both halves.

Usage: python3 scripts/exp_heisenberg.py
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

BASE = profile("whale")   # baseline: h_exit=0, h_entry_max=0 (H axis off)

# Pre-registered cells. (label, param overrides)
CELLS = [
    # H-A: leaky action integral, profit-immune (accumulates only while losing)
    *[(f"A ex={e} dec={d}", {"h_exit": e, "h_decay": d, "h_form": "action"})
      for e in (6.0, 10.0, 16.0) for d in (0.02, 0.05)],
    # H-B: literal dP * dT, dT = bars since the last new best_close / 24
    *[(f"B ex={e}", {"h_exit": e, "h_form": "product", "h_time_ref": 24.0})
      for e in (0.4, 0.6, 0.8)],
    # H-C: same as H-A but WITHOUT profit immunity — the direct test of the
    # runner-slaughter hypothesis (E9/E16/X-A/X-G failure mode).
    *[(f"C ex={e} no-immune", {"h_exit": e, "h_decay": 0.02, "h_immune_r": 99.0})
      for e in (10.0, 16.0)],
    # H-D: entry gate only — refuse to open into an already-ambiguous chart.
    *[(f"D entry<={m}", {"h_entry_max": m}) for m in (0.75, 0.85, 0.90)],
    # H-E: the uncertainty tax paid in SIZE — neither a release nor a refusal.
    *[(f"E k={k}", {"h_size_k": k}) for k in (0.3, 0.5, 0.8)],
]


def run(prep, p, window):
    frames, features, candidates, funding, states, corr = prep
    res = engine_run(frames, features, candidates, funding, states, corr, p,
                     start=window[0], end=window[1], equity0=10_000.0)
    return res, metrics(res["trades"], res["equity"], 10_000.0)


def row(prep, p):
    """full metrics + H1/H2 for the half-cross sign check + H-axis fire count."""
    resf, mf = run(prep, p, FULL)
    _, m1 = run(prep, p, H1)
    _, m2 = run(prep, p, H2)
    tr = resf["trades"]
    n_h = int((tr["reason"] == "Heisen").sum()) if len(tr) else 0
    return {
        "ret": mf.get("total_return"), "mdd": mf.get("max_dd"),
        "avg_r": mf.get("avg_r"), "n": mf.get("trades"), "n_h": n_h,
        "geo_m": mf.get("monthly_geo"), "bars": mf.get("avg_bars"),
        "h1_r": m1.get("avg_r"), "h2_r": m2.get("avg_r"),
        "h1_ret": m1.get("total_return"), "h2_ret": m2.get("total_return"),
    }


HDR = (f"{'cell':>18} | {'ret':>9} {'geo_m':>7} {'mdd':>8} {'avg_r':>7} "
       f"{'n':>4} {'nH':>4} {'bars':>6} | {'H1_r':>7} {'H2_r':>7} "
       f"{'H1_ret':>8} {'H2_ret':>8}")


def show(label, r):
    print(f"{label:>18} | {r['ret']:>9} {r['geo_m']:>7} {r['mdd']:>8} "
          f"{r['avg_r']:>7} {r['n']:>4} {r['n_h']:>4} {r['bars']:>6} | "
          f"{r['h1_r']:>7} {r['h2_r']:>7} {r['h1_ret']:>8} {r['h2_ret']:>8}")


def main():
    frames, funding = load_universe(DATA)
    features, candidates, states, corr = prepare(frames, funding, BASE)
    prep = (frames, features, candidates, funding, states, corr)

    print("=== E21 H axis sweep (whale, design window, half-cross) ===")
    print(HDR)
    base = row(prep, BASE)
    show("BASELINE", base)
    for label, kw in CELLS:
        show(label, row(prep, replace(BASE, **kw)))
    print("\nbaseline for reference:")
    show("BASELINE", base)


if __name__ == "__main__":
    main()
