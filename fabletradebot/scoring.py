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
