"""SR-D (E19) — forward slot-promotion governance.

The playbook matrix ships every experimental slot at a fixed paper scale
(risk_scale 0.20). E12 declared a "forward promotion" rule but never gave it
numbers; without them an experimental slot stays 0.20 forever and can never earn
Upgrade rights (risk_scale 1.0), so a genuinely-edged short can never take or
hold the single whale seat at full weight. This module is that rule, made
concrete and FORWARD-ONLY.

Design discipline (why this is governance, not curve-fitting):
  - It reads ONLY forward records — journal/shadow_ledger.csv by default, never
    the design window. shadow.py runs the SAME engine over the SAME live bars
    with only the seat/account governors lifted, so its ledger is forward
    evidence too, not a backtest (E19 gate holds); it just accumulates ~3x
    faster than forward_ledger.csv because every seat-blocked candidate closes
    out as a virtual trade instead of being discarded. forward_ledger.csv (the
    ledger REAL positions are sized from, see shadow.py) stays untouched by
    this switch — it is still appended every live close, just no longer the
    scale-promotion input.
  - Thresholds are INHERITED, not fitted: n>=30 / n>=60 mirror G2's trade-count
    floor, +0.05R mirrors G2's after-cost expectancy floor. No free parameter is
    tuned to any outcome.
  - It is MONOTONE and reversible: a slot climbs 0.20 -> 0.50 -> 1.0 as forward
    evidence accrues and is demoted one step the moment its rolling window turns
    negative. The proven anchor (BRK_L, base scale 1.0) is never touched.
  - Until the paper track accumulates n>=30 per slot it is INERT (every slot
    stays at its base scale) — applying it live changes nothing today; it only
    lets the forward record, as it grows, move size onto what actually works.
  - A slot must also be STABLE across its own chronological halves (both
    mean R > 0) before it earns a rung, mirroring the E15/E19c discipline used
    everywhere else in this repo. Without this, a slot could clear n>=30 on
    trades drawn almost entirely from a single regime stretch (shadow_ledger
    accumulates ~3x faster than forward_ledger, so this was live risk, not a
    hypothetical — the whale short slots were all TREND_DOWN candidates from
    one 8-day window when this gate was added) and earn size on a record that
    was never tested across a regime change.
"""
from __future__ import annotations

import os

import pandas as pd

LEDGER_PATH = "journal/forward_ledger.csv"
LEDGER_COLS = ["closed", "sym", "setup", "dir", "r", "pnl", "reason", "regime", "conf",
               # attribution-only, appended last so older rows stay readable:
               # peak_r - r is the give-back, the forward judge for the exit axis
               # (GIVEBACK_REDESIGN.md). No promotion logic reads it.
               "peak_r"]
# promotion's read source (shadow_ledger.csv, see the module docstring). Kept
# as its own constant, not shadow.LEDGER_PATH, so this module has no import
# dependency on shadow.py.
SCALE_SOURCE_PATH = "journal/shadow_ledger.csv"

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


def _stable_both_halves(r: pd.Series) -> bool:
    """Same-sign check across chronological halves (E15/E19c discipline): both
    the early and late half of the record must average R > 0. `r` must already
    be in chronological order. n<2 (nothing to split) is unstable by default."""
    n = len(r)
    if n < 2:
        return False
    mid = n // 2
    return bool(r.iloc[:mid].mean() > 0 and r.iloc[mid:].mean() > 0)


def _slot_scale(base: float, n: int, exp: float, roll: float, stable: bool) -> float:
    """Map one slot's forward record to its earned scale. Monotone ladder;
    demotion (rolling window negative) steps down one rung, never below base.
    `stable` gates promotion only — demotion never needs it (a rung already
    earned can still be pulled by the rolling window regardless)."""
    if base >= 1.0:                       # proven anchor — never demoted/altered
        return base
    earned = base
    if n >= PROMOTE_N1 and exp > PROMOTE_EXPECTANCY and stable:
        earned = 0.50
    if n >= PROMOTE_N2 and exp > PROMOTE_EXPECTANCY and stable:
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
        if "closed" in rows.columns:
            rows = rows.sort_values("closed")
        r = pd.to_numeric(rows["r"], errors="coerce").dropna()
        exp = float(r.mean()) if len(r) else 0.0
        roll = float(r.tail(ROLL_WINDOW).mean()) if len(r) else 0.0
        stable = _stable_both_halves(r)
        out[name] = _slot_scale(base, len(r), exp, roll, stable)
    return out


def apply_promotions(p, ledger: pd.DataFrame | None = None) -> list[tuple]:
    """Mutate p.playbooks risk_scale in place from the forward ledger. Returns
    the list of slots whose scale changed from base, for the run log. No-op when
    the ledger is empty/short (every slot resolves to its own base scale)."""
    if ledger is None:
        ledger = load_ledger(SCALE_SOURCE_PATH)
    changes = []
    for name, scale in promoted_scales(p.playbooks, ledger).items():
        base = float(p.playbooks[name].get("risk_scale", 1.0))
        if scale != base:
            p.playbooks[name] = {**p.playbooks[name], "risk_scale": scale}
            changes.append((name, base, scale))
    return changes
