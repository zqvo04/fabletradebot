"""CF-A leverage-tier score split — pre-registered measurement (CONF_REDESIGN §6).

Design window 2023-06-01~2026-01-31, whale profile, half-cross (H1/H2 split at
the temporal midpoint 2024-09-30 12:00).

PRIMARY CELL is the MATCHED-EXPOSURE ladder (0.63/0.81/0.95), whose boundaries
are the design-window c_base_pct quantiles that reproduce the CURRENT tier
population (6/16/34/44% at 2/3/5/10x). They are fitted to the score's
DISTRIBUTION, never to an outcome — the point is to hold the leverage mix fixed
and change only WHICH trade gets which tier, isolating the score's content from
the leverage LEVEL. Adopting a grid argmax is forbidden.

Gate (G5): terminal wealth / MDD / avg_r not degraded vs the whale baseline on
the full window AND on both halves; sign stability across the ladder sweep.

Usage: python3 scripts/exp_lev_tiers.py
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
BASE = profile("whale")


def ladder(a, b, c):
    return ((0.0, 2.0, 0.01), (a, 3.0, 0.01), (b, 5.0, 0.01), (c, 10.0, 0.01))


# primary = matched exposure; others are flatness / robustness probes
CELLS = {
    "matched .63/.81/.95": ladder(0.63, 0.81, 0.95),   # PRIMARY (a priori)
    "shift -.02":          ladder(0.61, 0.79, 0.93),
    "shift +.02":          ladder(0.65, 0.83, 0.97),
    "quartile .25/.50/.75": ladder(0.25, 0.50, 0.75),  # parameter-free control
}


def row(prep, p):
    frames, features, candidates, funding, states, corr = prep

    def m(w):
        r = engine_run(frames, features, candidates, funding, states, corr, p,
                       start=w[0], end=w[1], equity0=10_000.0)
        return metrics(r["trades"], r["equity"], 10_000.0)

    mf, m1, m2 = m(FULL), m((FULL[0], MID)), m((MID, FULL[1]))
    return {"ret": mf.get("total_return"), "mdd": mf.get("max_dd"),
            "avg_r": mf.get("avg_r"), "pf": mf.get("profit_factor"),
            "n": mf.get("trades"), "lev": mf.get("avg_leverage"),
            "h1_r": m1.get("avg_r"), "h1_ret": m1.get("total_return"),
            "h2_r": m2.get("avg_r"), "h2_ret": m2.get("total_return")}


HDR = (f"{'cell':>22} | {'ret':>9} {'mdd':>8} {'avg_r':>7} {'pf':>6} {'n':>4} "
       f"{'lev':>5} | {'H1_r':>7} {'H1_ret':>8} | {'H2_r':>7} {'H2_ret':>8}")


def show(label, r):
    print(f"{label:>22} | {r['ret']:>9} {r['mdd']:>8} {r['avg_r']:>7} {r['pf']:>6} "
          f"{r['n']:>4} {r['lev']:>5} | {r['h1_r']:>7} {r['h1_ret']:>8} | "
          f"{r['h2_r']:>7} {r['h2_ret']:>8}")


def main():
    frames, funding = load_universe(DATA)
    features, candidates, states, corr = prepare(frames, funding, BASE)
    prep = (frames, features, candidates, funding, states, corr)

    print("=== CF-A: leverage tier read from c_base_pct (whale, design window) ===")
    print(HDR)
    show("OFF (composite conf)", row(prep, BASE))
    for label, tiers in CELLS.items():
        show(label, row(prep, replace(BASE, lev_tiers=tiers)))

    print("\n=== primary cell — cost stress (G6) ===")
    print(HDR)
    show("OFF x2c", row(prep, replace(BASE, cost_mult=2.0)))
    show("matched x2c", row(prep, replace(BASE, lev_tiers=ladder(0.63, 0.81, 0.95),
                                          cost_mult=2.0)))


if __name__ == "__main__":
    main()
