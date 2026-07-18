"""SR-D (E19) — forward slot-promotion governance.

The playbook matrix ships every experimental slot at a fixed paper scale
(risk_scale 0.20). E12 declared a "forward promotion" rule but never gave it
numbers; without them an experimental slot stays 0.20 forever and can never earn
Upgrade rights (risk_scale 1.0), so a genuinely-edged short can never take or
hold the single whale seat at full weight. This module is that rule, made
concrete and FORWARD-ONLY.

Design discipline (why this is governance, not curve-fitting):
  - It reads ONLY the live forward ledger (journal/forward_ledger.csv), never
    the design window. Backtests never touch it, so base/whale design-window
    behaviour is invariant by construction (E19 gate).
  - Thresholds are INHERITED, not fitted: n>=30 / n>=60 mirror G2's trade-count
    floor, +0.05R mirrors G2's after-cost expectancy floor. No free parameter is
    tuned to any outcome.
  - It is MONOTONE and reversible: a slot climbs 0.20 -> 0.50 -> 1.0 as forward
    evidence accrues and is demoted one step the moment its rolling window turns
    negative. The proven anchor (BRK_L, base scale 1.0) is never touched.
  - Until the paper track accumulates n>=30 per slot it is INERT (every slot
    stays at its base scale) — applying it live changes nothing today; it only
    lets the forward record, as it grows, move size onto what actually works.
"""
from __future__ import annotations

import os

import pandas as pd

LEDGER_PATH = "journal/forward_ledger.csv"
LEDGER_COLS = ["closed", "sym", "setup", "dir", "r", "pnl", "reason", "regime", "conf"]

# promotion gates (inherited from G2, not fitted)
PROMOTE_N1, PROMOTE_N2 = 30, 60      # trade-count floors for 0.50 / 1.0
PROMOTE_EXPECTANCY = 0.05            # after-cost mean R floor (R already net)
ROLL_WINDOW = 30                     # rolling window for demotion
LADDER = (0.20, 0.50, 1.0)           # the only scales a slot may occupy


def append_trade(row: dict, path: str = LEDGER_PATH) -> None:
    """Append one closed forward trade to the ledger (create with header once).

    Called from the live loop under the same closed_keys de-dup guard that
    gates Notion/Telegram, so each trade is written exactly once.
    """
    rec = {k: row.get(k) for k in LEDGER_COLS}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = not os.path.exists(path)
    pd.DataFrame([rec]).to_csv(path, mode="a", header=header, index=False)


def load_ledger(path: str = LEDGER_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=LEDGER_COLS)
    return pd.read_csv(path)


def _slot_scale(base: float, n: int, exp: float, roll: float) -> float:
    """Map one slot's forward record to its earned scale. Monotone ladder;
    demotion (rolling window negative) steps down one rung, never below base."""
    if base >= 1.0:                       # proven anchor — never demoted/altered
        return base
    earned = base
    if n >= PROMOTE_N1 and exp > PROMOTE_EXPECTANCY:
        earned = 0.50
    if n >= PROMOTE_N2 and exp > PROMOTE_EXPECTANCY:
        earned = 1.0
    # demotion: a sustained negative rolling window drops one rung (floor=base)
    if n >= ROLL_WINDOW and roll < 0.0:
        i = LADDER.index(earned) if earned in LADDER else 0
        earned = LADDER[max(0, i - 1)]
    return max(base, earned)


def promoted_scales(playbooks: dict, ledger: pd.DataFrame) -> dict[str, float]:
    """Earned risk_scale per slot from the forward ledger. Slots with < N1
    forward trades keep their base scale (inert), so an empty ledger returns
    every base scale unchanged."""
    out: dict[str, float] = {}
    for name, pb in playbooks.items():
        base = float(pb.get("risk_scale", 1.0))
        rows = ledger[ledger["setup"] == name] if len(ledger) else ledger
        n = len(rows)
        if n == 0:
            out[name] = base
            continue
        r = pd.to_numeric(rows["r"], errors="coerce").dropna()
        exp = float(r.mean()) if len(r) else 0.0
        roll = float(r.tail(ROLL_WINDOW).mean()) if len(r) else 0.0
        out[name] = _slot_scale(base, len(r), exp, roll)
    return out


def apply_promotions(p, ledger: pd.DataFrame | None = None) -> list[tuple]:
    """Mutate p.playbooks risk_scale in place from the forward ledger. Returns
    the list of slots whose scale changed from base, for the run log. No-op when
    the ledger is empty/short (every slot resolves to its own base scale)."""
    if ledger is None:
        ledger = load_ledger()
    changes = []
    for name, scale in promoted_scales(p.playbooks, ledger).items():
        base = float(p.playbooks[name].get("risk_scale", 1.0))
        if scale != base:
            p.playbooks[name] = {**p.playbooks[name], "risk_scale": scale}
            changes.append((name, base, scale))
    return changes
