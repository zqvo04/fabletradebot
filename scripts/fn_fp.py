"""False-negative / false-positive analysis of the entry filters (E14).

A trade signal is a binary classifier: "will the next N hours move in our
favour by >= the cost hurdle?" Each filter (regime gate, TF alignment, volume
confirmation, event/precision trigger) trades one error type against the other:
  - FALSE POSITIVE  = we entered and it went the wrong way (a loss).
  - FALSE NEGATIVE  = a filter blocked us but the move would have been a winner.
We estimate both by, for each candidate BAR, labelling the forward outcome and
comparing the population a filter ADMITS vs the population it REJECTS.

Reported per playbook family and per marginal filter so we can see which knob
buys precision (fewer FP) at what recall cost (more FN). BTC + a couple of
liquid alts; forward horizon = the playbook's natural hold.
"""
import sys

sys.path.insert(0, ".")
import numpy as np
import pandas as pd

from fabletradebot.backtest import prepare
from fabletradebot.config import Params
from fabletradebot.data_okx import load_1h, load_funding

SYMS = ["BTC", "ETH", "SOL"]
HORIZON = 24          # bars ahead to judge the "truth" of an entry
HURDLE = 0.004        # move must clear ~2x round-trip cost to count as a win


def label_forward(df: pd.DataFrame, direction: int, horizon: int) -> pd.Series:
    """1 if the best favourable excursion over `horizon` clears the hurdle
    before the worst adverse excursion does (a tradeable win), else 0."""
    c = df["close"]
    fwd_hi = df["high"].shift(-1).rolling(horizon).max().shift(-(horizon - 1))
    fwd_lo = df["low"].shift(-1).rolling(horizon).min().shift(-(horizon - 1))
    if direction == 1:
        up = (fwd_hi - c) / c
        dn = (c - fwd_lo) / c
    else:
        up = (c - fwd_lo) / c
        dn = (fwd_hi - c) / c
    return ((up >= HURDLE) & (up >= dn)).astype(float)


def main():
    p = Params()
    frames = {s: load_1h(s, "data") for s in SYMS}
    frames = {s: d[d.index <= pd.Timestamp("2026-01-31", tz="UTC")] for s, d in frames.items()}
    funding = {s: load_funding(s, "data") for s in SYMS}
    features, candidates, states, corr = prepare(frames, funding, p)

    rows = []
    for s in SYMS:
        cand = candidates[s]
        if len(cand) == 0:
            continue
        for d in (1, -1):
            truth = label_forward(frames[s], d, HORIZON)
            sub = cand[cand["dir"] == d]
            if len(sub) == 0:
                continue
            admitted = truth.reindex(sub.index).dropna()
            # baseline: every bar in the same regime states this slot admits
            for setup in sub["setup"].unique():
                idx = sub[sub["setup"] == setup].index
                y = truth.reindex(idx).dropna()
                if len(y) < 8:
                    continue
                fp = 1 - y.mean()          # admitted but not a win
                rows.append({"sym": s, "setup": setup, "n": len(y),
                             "win_rate": round(y.mean(), 3),
                             "false_positive": round(fp, 3)})
    fp_tbl = pd.DataFrame(rows)
    print("=== FALSE POSITIVE by playbook (admitted entries that were NOT "
          f"tradeable wins over {HORIZON}h, hurdle {HURDLE*100:.1f}%) ===")
    if len(fp_tbl):
        agg = fp_tbl.groupby("setup").apply(
            lambda x: pd.Series({"n": x.n.sum(),
                                 "win_rate": np.average(x.win_rate, weights=x.n),
                                 "false_positive": np.average(x.false_positive, weights=x.n)}),
            include_groups=False).round(3)
        print(agg.sort_values("false_positive").to_string())

    # marginal filter FN/FP: start from the RAW 7d-breakout population on BTC
    # (before any alignment/volume gate) and add one filter at a time. A good
    # filter LOWERS the false-positive rate among what it admits while the
    # entries it rejects (its false-negative cost) win LESS than the admits.
    print("\n=== MARGINAL FILTER FN/FP (raw 7d breakout long, BTC) ===")
    s = "BTC"
    f = features[s]
    truth = label_forward(frames[s], 1, HORIZON)
    raw = (f["close"] > f["hh"]).fillna(False)
    filters = {
        "1D trend up (bias1d==1)": (f["bias1d"] == 1),
        "4H trend up (bias4h==1)": (f["bias4h"] == 1),
        "volume >= 1.2x median": (f["volume"] / f["vol_med"]) >= p.brk_vol_mult,
    }
    base_win = truth.reindex(f.index[raw]).dropna().mean()
    print(f"  raw breakout: n={int(raw.sum())} win={base_win:.2f} (FP={1-base_win:.2f})")
    for fname, filt in filters.items():
        filt = filt.reindex(f.index).fillna(False).astype(bool)
        admit = raw & filt
        reject = raw & ~filt
        ya = truth.reindex(f.index[admit]).dropna()
        yr = truth.reindex(f.index[reject]).dropna()
        if len(ya) < 5 or len(yr) < 5:
            print(f"  + {fname}: n admit={len(ya)} reject={len(yr)} (too few to judge)")
            continue
        print(f"  + {fname}: admit n={len(ya)} win={ya.mean():.2f} (FP={1-ya.mean():.2f}) "
              f"vs reject n={len(yr)} win_if_taken={yr.mean():.2f} (FN cost={yr.mean():.2f}) "
              f"-> {'GOOD' if ya.mean() > yr.mean() else 'NO EDGE'}")


if __name__ == "__main__":
    main()
