"""Scoring — diagnostics separated from trading logic (BLUEPRINT §10 / brief §10).

Consumes the trades table the engine produced and prints/serializes honest
breakdowns: overall, per regime, per asset, per setup, per confidence tier.
"""
from __future__ import annotations

import pandas as pd

from .backtest import breakdown, metrics


def conf_tier_label(conf: float) -> str:
    for lo, hi, name in ((0.9, 10, "T4 0.90+"), (0.8, 0.9, "T3 0.80"),
                         (0.7, 0.8, "T2 0.70"), (0.6, 0.7, "T1 0.60")):
        if conf >= lo:
            return name
    return "below"


def mark_to_market(pos, price: float) -> dict:
    """Unrealized scoring of one open Position at the latest price."""
    avg_e = pos.avg_entry()
    price_pct = pos.direction * (price - avg_e) / avg_e * 100
    unreal = pos.gross_at(price) + pos.realized
    r = unreal / pos.risk_amt if pos.risk_amt > 0 else 0.0
    return {"sym": pos.sym, "setup": pos.setup, "regime": pos.regime,
            "dir": pos.direction, "leverage": pos.leverage, "bars": pos.bars,
            "price": price, "sl": pos.sl, "r": r,
            "pnl_pct_price": price_pct, "pnl_pct_lev": price_pct * pos.leverage,
            "risk_amt": pos.risk_amt, "hold_conf": round(getattr(pos, "hold_conf", 0.0), 3)}


def open_report(open_pos: dict, prices: dict) -> str:
    """Hourly scoring of the currently OPEN positions (runs every step,
    alongside the trade loop, per brief §10)."""
    if not open_pos:
        return "== open positions == none"
    rows = [mark_to_market(p, prices[s]) for s, p in open_pos.items() if s in prices]
    if not rows:
        return "== open positions == none priced"
    tot_r = sum(x["r"] for x in rows)
    lines = [f"== open positions ({len(rows)}) | unrealized {tot_r:+.2f}R =="]
    for x in sorted(rows, key=lambda z: z["r"], reverse=True):
        d = "L" if x["dir"] > 0 else "S"
        lines.append(f"  {x['sym']:5s} {x['setup']:6s} {d} {x['leverage']:.0f}x "
                     f"{x['r']:+.2f}R  price {x['pnl_pct_price']:+.2f}%  "
                     f"held {x['bars']}h  regime {x['regime']}  "
                     f"hold_conf {x['hold_conf']:.2f}")
    return "\n".join(lines)


def seat_report(trades: pd.DataFrame) -> str:
    """V5.1 Phase 0 — seat-time occupancy vs R contribution per slot (the §2a
    seat-distortion diagnostic, forward version). A slot holding a large share of
    the single seat's time while contributing little/negative R is the I5
    redistribution target; this is the forward judge (E12) that decides it — the
    design window may not."""
    if len(trades) == 0 or "bars" not in trades:
        return "== seat time vs R == (no trades)"
    g = trades.groupby("setup")
    bars = g["bars"].sum()
    r = g["r"].sum()
    n = g.size()
    tot_bars, tot_r = bars.sum(), r.sum()
    tab = pd.DataFrame({
        "n": n,
        "seat_bars": bars,
        "seat_share": (bars / tot_bars).round(4) if tot_bars else 0.0,
        "sum_r": r.round(3),
        "r_share": (r / tot_r).round(4) if tot_r else 0.0,
        "r_per_bar": (r / bars.replace(0, pd.NA)).round(5),
    }).sort_values("seat_share", ascending=False)
    return "== seat time vs R contribution ==\n" + tab.to_string()


def conf_monotonicity(trades: pd.DataFrame, q: int = 5) -> str:
    """V5.1 Phase 0 — is realized R monotone in conf quantile? (E17 §3D carried
    forward). If higher conf buckets do not earn higher mean R, conf is not
    ranking edge and any conf-ordered seat/tier logic is unbacked."""
    if len(trades) < q:
        return "== conf quantile-R == (too few trades)"
    t = trades[["conf", "r"]].copy()
    try:
        t["bucket"] = pd.qcut(t["conf"], q, labels=False, duplicates="drop")
    except ValueError:
        return "== conf quantile-R == (conf not separable)"
    g = t.groupby("bucket")
    tab = pd.DataFrame({"n": g.size(), "conf_lo": g["conf"].min().round(4),
                        "conf_hi": g["conf"].max().round(4),
                        "mean_r": g["r"].mean().round(4)})
    corr = t["conf"].corr(t["r"])
    return (f"== conf quantile-R (corr={corr:.4f}) ==\n" + tab.to_string())


def hold_entry_report(trades: pd.DataFrame) -> str:
    """V5.1 Phase 0 — hold_confidence AT ENTRY vs realized R (WF-A forward
    judge). Armed profiles gate hold_entry >= hold_conf_exit, so the surviving
    distribution and corr(hold_entry, R) show whether the coherence gate keeps
    the edge it was measured to add."""
    if len(trades) == 0 or "hold_entry" not in trades:
        return "== hold_entry vs R == (not recorded)"
    h = trades["hold_entry"]
    if h.nunique() <= 1:                       # disarmed profile (constant 1.0)
        return f"== hold_entry vs R == disarmed (constant {h.iloc[0]})"
    corr = h.corr(trades["r"])
    desc = h.describe()[["min", "25%", "50%", "75%", "max"]].round(3)
    return (f"== hold_entry vs R (corr={corr:.4f}) ==\n"
            f"  dist: min={desc['min']} q25={desc['25%']} med={desc['50%']} "
            f"q75={desc['75%']} max={desc['max']}")


def score_report(trades: pd.DataFrame, equity: pd.Series, equity0: float) -> str:
    if len(trades) == 0:
        return "no closed trades yet"
    t = trades.copy()
    t["tier"] = t["conf"].map(conf_tier_label)
    m = metrics(t, equity, equity0)
    lines = ["== overall ==",
             ", ".join(f"{k}={v}" for k, v in m.items()),
             ""]
    for by in ("setup", "regime", "tier", "sym", "reason"):
        lines.append(f"== by {by} ==")
        lines.append(breakdown(t, by).to_string())
        lines.append("")
    # V5.1 Phase 0 forward-judge diagnostics
    lines.append(seat_report(t))
    lines.append("")
    lines.append(conf_monotonicity(t))
    lines.append("")
    lines.append(hold_entry_report(t))
    return "\n".join(lines)
