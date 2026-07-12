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
    return "\n".join(lines)
