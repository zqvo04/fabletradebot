"""SR-D (E19) forward slot-promotion governance.

Pins the properties that keep this from becoming curve-fitting:
  - an empty/short forward ledger is INERT (every slot keeps its base scale),
    so applying it live changes nothing until forward evidence accrues;
  - the ladder is monotone in forward n and after-cost expectancy, and the
    proven anchor (BRK_L, base 1.0) is never altered;
  - a sustained negative rolling window demotes exactly one rung, never below
    the slot's base scale.
"""
import pandas as pd

from fabletradebot.config import profile
from fabletradebot.promotion import (LEDGER_COLS, _slot_scale, apply_promotions,
                                      promoted_scales)


def _ledger(setup: str, rs: list[float]) -> pd.DataFrame:
    return pd.DataFrame([{**{c: None for c in LEDGER_COLS}, "setup": setup, "r": r}
                         for r in rs])


def test_empty_ledger_is_inert():
    p = profile("whale")
    base = {k: v.get("risk_scale", 1.0) for k, v in p.playbooks.items()}
    scales = promoted_scales(p.playbooks, pd.DataFrame(columns=LEDGER_COLS))
    assert scales == base
    assert apply_promotions(p, pd.DataFrame(columns=LEDGER_COLS)) == []
    assert {k: v.get("risk_scale", 1.0) for k, v in p.playbooks.items()} == base


def test_short_track_stays_at_base():
    # 29 winning trades — below the n>=30 floor — must not promote yet
    scale = _slot_scale(0.20, n=29, exp=0.5, roll=0.5)
    assert scale == 0.20


def test_promotion_ladder_monotone():
    assert _slot_scale(0.20, n=30, exp=0.06, roll=0.06) == 0.50   # earns first rung
    assert _slot_scale(0.20, n=60, exp=0.06, roll=0.06) == 1.0    # earns Upgrade rights
    # positive n but sub-floor expectancy never promotes
    assert _slot_scale(0.20, n=80, exp=0.04, roll=0.04) == 0.20


def test_demotion_one_rung_floored():
    # promoted to 1.0 on lifetime edge but rolling window turned negative
    assert _slot_scale(0.20, n=80, exp=0.06, roll=-0.02) == 0.50
    # a 0.50 slot that rolls negative falls back to base, never below
    assert _slot_scale(0.20, n=40, exp=0.06, roll=-0.02) == 0.20


def test_proven_anchor_never_altered():
    for n, exp, roll in [(0, 0.0, 0.0), (200, -0.5, -0.5), (200, 0.9, 0.9)]:
        assert _slot_scale(1.0, n=n, exp=exp, roll=roll) == 1.0


def test_apply_promotes_real_slot_and_reports():
    p = profile("whale")
    led = _ledger("PBK_S", [0.10] * 60)          # 60 forward wins @ +0.10R
    changes = apply_promotions(p, led)
    assert ("PBK_S", 0.20, 1.0) in changes
    assert p.playbooks["PBK_S"]["risk_scale"] == 1.0
    assert p.playbooks["BRK_L"]["risk_scale"] == 1.0   # anchor untouched
