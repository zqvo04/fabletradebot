"""T6 (STOP_REDESIGN §12): loss-path taxonomy + separability of loss-side cuts.

Replays whale baseline on the design window, rebuilds each trade's per-bar path
from the engine's own feature frames (1R = |entry - sl0|, exactly the engine's
unreal_r since whale has no pyramid/TP), classifies losers, and scores candidate
early-cut states by R saved on losers vs R forfeited on winners (path-ignoring,
per-trade — compounding/seat effects are NOT in here; engine replay judges).
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

import numpy as np
import pandas as pd

from exp_stop_common import BASE, FULL, MID, load
from fabletradebot.engine import run as engine_run

LOSS_REASONS = ("SL", "LossFade", "BiasFlip")


def paths(prep, trades):
    frames, features = prep[0], prep[1]
    out = []
    for k, t in trades.iterrows():
        f = features[t.sym].loc[t.opened:t.closed]
        if t.reason == "Upgrade":          # closed at bar open, last bar never managed
            f = f.iloc[:-1]
        d, e, one_r = t.dir, t.entry, abs(t.entry - t.sl0)
        bar = pd.DataFrame({
            "k": np.arange(1, len(f) + 1),
            "ur": d * (f.close - e) / one_r,                            # close R
            "adv": d * ((f.low if d == 1 else f.high) - e) / one_r,     # intrabar worst R
            "hold": f["hold_L" if d == 1 else "hold_S"],
            "mom": f["mom_L" if d == 1 else "mom_S"],
            "b4": f.bias4h * d,                                         # +1 aligned
            "ema": d * (f.close - f.ema20_4h) / f.atr4h,                # >0 right side
            "vol": f.volume / f.vol_med,
        }, index=f.index)
        a0 = features[t.sym].atr1h.loc[:t.opened].iloc[-2]              # decision-bar ATR
        bar["atr_chg"] = f.atr1h / a0
        if t.reason in ("SL", "Trail"):    # stopped intrabar: last close never seen
            bar.iloc[-1, bar.columns.get_loc("ur")] = np.nan
        bar["mae"] = bar[["adv"]].min(axis=1).cummin()                  # intrabar MAE so far
        bar["peak"] = bar.ur.cummax().ffill().clip(lower=0)
        bar["tid"] = k
        out.append(bar)
    return pd.concat(out)


def classify(t):
    if t.peak_r >= 1.0:
        return "d_giveback"
    if t.peak_r > 0.5:
        return "c_roundtrip"
    if t.bars <= 6:
        return "a_fast" if t.peak_r <= 0.1 else "a2_fast_bump"
    return "b_bleed"


def class_table(tr):
    los = tr[tr.reason.isin(LOSS_REASONS) & (tr.r < 0)].copy()
    los["cls"] = los.apply(classify, axis=1)
    tot = los.r.sum()
    print(f"\n== losers (SL/LossFade/BiasFlip, r<0): n={len(los)} sum={tot:.1f}R "
          f"(all r<0 trades n={(tr.r < 0).sum()} sum={tr.r[tr.r < 0].sum():.1f}R)")
    g = los.groupby("cls").r.agg(n="size", sum="sum", mean="mean")
    g["share"] = g["sum"] / tot
    g["bars_med"] = los.groupby("cls").bars.median()
    print(g.round(3))
    print("\nby reason x class (n / sumR)")
    print(los.pivot_table(index="cls", columns="reason", values="r", aggfunc=["size", "sum"]).round(1))
    print("\nby setup x class (n / sumR)")
    print(los.pivot_table(index="setup", columns="cls", values="r", aggfunc="size").fillna(0).astype(int))
    print(los.pivot_table(index="setup", columns="cls", values="r", aggfunc="sum").round(1))
    los["half"] = np.where(los.opened < MID, "H1", "H2")
    print("\nby half x class (n / sumR)")
    print(los.pivot_table(index="half", columns="cls", values="r", aggfunc=["size", "sum"]).round(1))
    return los


def cut_scan(tr, pb, name, cond, fee_r):
    """First bar where cond holds -> exit at that bar's close R (minus fee_r).
    Returns saved R on eventual losers, forfeited R on eventual winners."""
    hit = pb[cond(pb)].groupby("tid").head(1).set_index("tid")
    j = tr.loc[hit.index].assign(exit_r=hit.ur - fee_r)
    los, win = j[j.r <= 0], j[j.r > 0]
    saved = (los.exit_r - los.r).sum()
    forfeit = (win.r - win.exit_r).sum()
    runners = (win.r >= 6).sum()
    alive = tr.index.isin(hit.index)
    # payoff of the counterfactual book (per-trade, path-ignoring)
    r_new = tr.r.copy()
    r_new.loc[j.index] = j.exit_r
    w, l = r_new[r_new > 0], r_new[r_new <= 0]
    pay = w.mean() / -l.mean()
    return {"rule": name, "fires": len(j), "los": len(los), "win": len(win),
            "win_frac": round(len(win) / max(1, len(j)), 3),
            "win_r_med": round(win.r.median(), 2) if len(win) else 0,
            "runners>=6R": runners, "saved": round(saved, 1),
            "forfeit": round(forfeit, 1), "net": round(saved - forfeit, 1),
            "ratio": round(saved / forfeit, 2) if forfeit > 0 else np.inf,
            "payoff_cf": round(pay, 3), "sumR_cf": round(r_new.sum(), 1)}


def split_scan(tr, pb, x, fee_r):
    """Two-stage stop: half the size exits the first time intrabar adverse <= -x."""
    hit = pb[pb.adv <= -x].groupby("tid").head(1).set_index("tid")
    j = tr.loc[hit.index]
    exit_r = -x - fee_r
    delta = 0.5 * (exit_r - j.r)          # >0 helps (loser), <0 hurts (winner)
    los, win = delta[j.r <= 0], delta[j.r > 0]
    r_new = tr.r.copy()
    r_new.loc[j.index] = j.r + delta
    w, l = r_new[r_new > 0], r_new[r_new <= 0]
    return {"rule": f"split50@-{x}", "fires": len(j), "los": len(los), "win": len(win),
            "win_frac": round(len(win) / max(1, len(j)), 3),
            "win_r_med": round(j.r[j.r > 0].median(), 2) if len(win) else 0,
            "runners>=6R": int((j.r >= 6).sum()),
            "saved": round(los.sum(), 1), "forfeit": round(-win.sum(), 1),
            "net": round(delta.sum(), 1),
            "ratio": round(los.sum() / -win.sum(), 2) if win.sum() < 0 else np.inf,
            "payoff_cf": round(w.mean() / -l.mean(), 3), "sumR_cf": round(r_new.sum(), 1)}


def state_table(tr, pb):
    """Among trades still open at bar k with close-R <= -x: who ends a winner?"""
    rows = []
    for k in (1, 2, 3, 6, 12, 24):
        at = pb[pb.k == k].set_index("tid")
        for x in (0.3, 0.5, 0.7):
            s = at[at.ur <= -x]
            fin = tr.loc[s.index].r
            rows.append({"bar": k, "close_R<=": -x, "n": len(s),
                         "win_frac": round((fin > 0).mean(), 3) if len(s) else np.nan,
                         "win_sumR": round(fin[fin > 0].sum(), 1),
                         "runners>=6R": int((fin >= 6).sum()),
                         "loser_meanR": round(fin[fin <= 0].mean(), 3) if (fin <= 0).any() else np.nan})
    print("\n== state table: open at bar k with close R <= -x")
    print(pd.DataFrame(rows).to_string(index=False))


def observable_table(tr, pb):
    """At first bar with close R <= -0.5 (and peak<1): observable split, winners vs losers."""
    hit = pb[(pb.ur <= -0.5) & (pb.peak < 1)].groupby("tid").head(1).set_index("tid")
    hit["win"] = tr.loc[hit.index].r > 0
    print(f"\n== observables at first close<=-0.5R (n={len(hit)}, winners={hit.win.sum()})")
    print(hit.groupby("win")[["k", "mae", "hold", "mom", "b4", "ema", "atr_chg", "vol"]]
          .median().round(3))
    def auc(col, sign):
        a, b = hit.loc[~hit.win, col].dropna() * sign, hit.loc[hit.win, col].dropna() * sign
        return round(float((a.values[:, None] > b.values[None, :]).mean()
                           + 0.5 * (a.values[:, None] == b.values[None, :]).mean()), 3)
    # AUC: P(loser value "more broken" than winner value); 0.5 = no separation
    print("AUC(loser more adverse than winner):",
          {c: auc(c, s) for c, s in [("hold", -1), ("mom", -1), ("ema", -1), ("b4", -1),
                                     ("k", -1), ("mae", -1), ("atr_chg", 1), ("vol", 1)]})


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    prep = load()
    res = engine_run(*prep, BASE, start=FULL[0], end=FULL[1], equity0=10_000.0)
    tr = res["trades"].reset_index(drop=True)
    pb = paths(prep, tr)
    # sanity: rebuilt close-path peak equals the engine's recorded peak_r
    chk = pb.groupby("tid").ur.max().clip(lower=0)
    print("peak_r rebuild max abs err:", float((chk - tr.peak_r).abs().max()))
    class_table(tr)
    state_table(tr, pb)
    observable_table(tr, pb)
    # round-trip fee in R ~ 2 * fee * notional / risk = 2*fee/stop_frac; take median
    fee_r = float((2 * BASE.taker_fee * BASE.cost_mult
                   / ((tr.entry - tr.sl0).abs() / tr.entry)).median())
    print(f"\nfee_r (median round-trip fee in R) = {fee_r:.3f}")
    rules = []
    pre1 = pb.peak < 1
    for x in (0.3, 0.5, 0.7):
        for K in (3, 6, 12, 24, 10**6):
            rules.append(cut_scan(tr, pb, f"close<=-{x} by k<={K if K < 10**6 else 'any'}",
                                  lambda b, x=x, K=K: (b.ur <= -x) & (b.k <= K) & pre1, fee_r))
    for x in (0.5,):
        rules.append(cut_scan(tr, pb, f"close<=-{x} & mom<=0.25",
                              lambda b, x=x: (b.ur <= -x) & (b.mom <= 0.25) & pre1, fee_r))
        rules.append(cut_scan(tr, pb, f"close<=-{x} & b4<0",
                              lambda b, x=x: (b.ur <= -x) & (b.b4 < 0) & pre1, fee_r))
        rules.append(cut_scan(tr, pb, f"close<=-{x} & ema<-0.5",
                              lambda b, x=x: (b.ur <= -x) & (b.ema < -0.5) & pre1, fee_r))
        rules.append(cut_scan(tr, pb, f"close<=-{x} & hold<0.6",
                              lambda b, x=x: (b.ur <= -x) & (b.hold < 0.6) & pre1, fee_r))
        rules.append(cut_scan(tr, pb, f"close<=-{x} & peak<=0.1 & k<=6",
                              lambda b, x=x: (b.ur <= -x) & (b.peak <= 0.1) & (b.k <= 6), fee_r))
    for x in (0.3, 0.5, 0.7):
        rules.append(split_scan(tr, pb, x, fee_r))
    print("\n== cut/split candidates (path-ignoring, per-trade)")
    print(f"baseline sumR={tr.r.sum():.1f} payoff={tr.r[tr.r > 0].mean() / -tr.r[tr.r <= 0].mean():.3f}")
    print(pd.DataFrame(rules).to_string(index=False))
