"""Regime label test on 4H bars vs the current 1D classifier (REGIME_REDESIGN §8 method).

Pre-registered cells (fixed before running, no tuning afterwards):
  D1    current production: 1D bars, EMA20/50/100, hysteresis 2 bars.
  H4-T  same TIME constants on 4H bars: every length x6, hysteresis 12 bars,
        same thresholds. Isolates update granularity (4h vs 24h).
  H4-B  same BAR counts on 4H bars: lengths as-is, hysteresis 2 bars; return
        thresholds scaled by sqrt(4/24) (vol-time). A genuinely faster regime.

Per 1H bar t (state = latest CLOSED regime bar, BTC CRISIS override, i.e. the
exact production projection) -> forward close return at +4/+24/+72/+168h.
Week-block 95% CI. Judge: TREND_UP +168h CI excludes 0 and
sep = TREND_UP - pooled(rest) at +168h; plus transitions/asset/day (alert noise)
and both chronological halves of the window.

usage: python scripts/exp_regime_4h.py [data_dir]
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from fabletradebot.backtest import load_universe  # noqa: E402
from fabletradebot.config import profile  # noqa: E402
from fabletradebot.data_okx import closed_asof_1h, resample  # noqa: E402
from fabletradebot.indicators import atr, ema, pct_rank, realized_vol  # noqa: E402
from fabletradebot.regime import apply_hysteresis, effective_state, raw_regime_1d  # noqa: E402
from fabletradebot.validation import week_block_ci  # noqa: E402

START, END = "2023-06-01", "2026-01-01"          # design window (STOP_REDESIGN §8)
HORIZONS = (4, 24, 72, 168)
S = (4 / 24) ** 0.5
CELLS = {  # bar_h, length mult, hysteresis, (crash_1bar, crash_5bar, crisis_5bar)
    "D1":   (24, 1, 2, (-0.07, -0.12, -0.08)),
    "H4-T": (4, 6, 12, (-0.07, -0.12, -0.08)),
    "H4-B": (4, 1, 2, (-0.07 * S, -0.12 * S, -0.08 * S)),
}


def raw_state(d: pd.DataFrame, k: int, th: tuple, p) -> pd.Series:
    """raw_regime_1d with every window scaled by k and return thresholds `th`."""
    c = d["close"]
    e20, e50, e100 = ema(c, 20 * k), ema(c, 50 * k), ema(c, 100 * k)
    vol_pct = pct_rank(realized_vol(c, 20 * k), 365 * k)
    r5, r1 = c.pct_change(5 * k), c.pct_change(k)
    crisis = (r5 < th[1]) | (r1 < th[0]) | ((vol_pct > p.vol_pct_crisis) & (r5 < th[2]))
    strength = (e20 - e50).abs() / atr(d, 14 * k) > 0.5
    st = pd.Series("RANGE", index=d.index)
    st[strength & (c > e100) & (e20 > e50)] = "TREND_UP"
    st[strength & (c < e100) & (e20 < e50)] = "TREND_DOWN"
    st[vol_pct > p.vol_pct_highvol] = "HIGH_VOL"
    st[crisis] = "CRISIS"
    return st


def states_1h(frames: dict, cell: str, p) -> dict:
    bar_h, k, hyst, th = CELLS[cell]
    def one(df):
        d = resample(df, bar_h)
        return closed_asof_1h(apply_hysteresis(raw_state(d, k, th, p), hyst),
                              bar_h, df.index).fillna("RANGE")
    btc = one(frames["BTC"])
    return {s: effective_state(one(df), btc) for s, df in frames.items()}


def table(frames: dict, states: dict) -> pd.DataFrame:
    rows = []
    for s, df in frames.items():
        c = df["close"]
        out = pd.DataFrame({"opened": df.index, "sym": s, "state": states[s].values})
        for h in HORIZONS:
            out[f"f{h}"] = (c.shift(-h) / c - 1).values * 100
        rows.append(out)
    t = pd.concat(rows)
    return t[(t.opened >= pd.Timestamp(START, tz="UTC")) & (t.opened < pd.Timestamp(END, tz="UTC"))]


def report(cell: str, t: pd.DataFrame, states: dict) -> None:
    print(f"\n=== {cell} ===  n={len(t):,}")
    print(f"{'state':11}{'n':>8}{'wk':>5} |" + "".join(f"{'+%dh' % h:>8}" for h in HORIZONS) + "   +168h CI")
    for st, g in t.groupby("state"):
        g = g.dropna(subset=["f168"])
        lo, hi, wk = week_block_ci(g, "f168", n_boot=2000)
        star = " *" if lo > 0 or hi < 0 else ""
        print(f"{st:11}{len(g):>8,}{wk:>5} |" + "".join(f"{g[f'f{h}'].mean():>+8.2f}" for h in HORIZONS)
              + f"   [{lo:+.2f},{hi:+.2f}]{star}")
    t = t.dropna(subset=["f168"]).sort_values("opened")
    mid = t.opened.iloc[len(t) // 2]
    for name, part in (("all", t), ("H1", t[t.opened < mid]), ("H2", t[t.opened >= mid])):
        up = part.state == "TREND_UP"
        print(f"  sep[{name}] TREND_UP - rest: +72h {part.f72[up].mean() - part.f72[~up].mean():+.2f}"
              f"  +168h {part.f168[up].mean() - part.f168[~up].mean():+.2f}"
              f"  (TREND_UP share {up.mean():.0%})")
    win = slice(START, END)
    n_ch = sum(int((x[win] != x[win].shift()).iloc[1:].sum()) for x in states.values())
    days = (pd.Timestamp(END) - pd.Timestamp(START)).days
    print(f"  transitions: {n_ch} total, {n_ch / days:.2f}/day (all assets), "
          f"{n_ch / days / len(states):.3f}/asset/day")


def main() -> None:
    p = profile("whale")
    frames, _ = load_universe(sys.argv[1] if len(sys.argv) > 1 else "data")
    # self-check: the parametrised classifier IS production at D1 settings
    d = resample(frames["BTC"], 24)
    assert raw_state(d, 1, CELLS["D1"][3], p).equals(raw_regime_1d(d, p)["raw_state"])
    assert p.hysteresis_bars == CELLS["D1"][2]
    print(f"universe {list(frames)} | window {START}..{END}")
    for cell in CELLS:
        st = states_1h(frames, cell, p)
        report(cell, table(frames, st), st)


if __name__ == "__main__":
    main()
