"""V5.1 Phase 0 forward-judge diagnostics (scoring.py): seat-time vs R,
conf quantile-R monotonicity, hold_entry-vs-R. Diagnostics only — they read the
trades table and must never crash on empty / degenerate input."""
import pandas as pd

from fabletradebot.scoring import (conf_monotonicity, hold_entry_report,
                                   seat_report)


def _trades():
    n = 20
    return pd.DataFrame({
        "setup": (["BRK_L"] * 10 + ["PBK_L"] * 10),
        "bars": [50] * 10 + [10] * 10,
        "r": [1.0, -0.5] * 5 + [-0.3, 0.1] * 5,
        "conf": [0.6 + 0.01 * i for i in range(n)],
        "hold_entry": [0.5 + 0.02 * i for i in range(n)],
    })


def test_seat_report_shares_sum_and_rank():
    out = seat_report(_trades())
    assert "seat time vs R" in out
    # BRK_L holds 50/60 of seat-bars -> it must head the table (sorted by share)
    assert out.index("BRK_L") < out.index("PBK_L")


def test_conf_monotonicity_reports_corr():
    out = conf_monotonicity(_trades(), q=4)
    assert "corr=" in out and "quantile-R" in out


def test_hold_entry_report_armed_vs_disarmed():
    armed = hold_entry_report(_trades())
    assert "corr=" in armed and "dist:" in armed
    # a disarmed profile stamps a constant 1.0 -> reported as disarmed, no corr
    dis = _trades().assign(hold_entry=1.0)
    assert "disarmed" in hold_entry_report(dis)


def test_diagnostics_survive_empty():
    empty = pd.DataFrame(columns=["setup", "bars", "r", "conf", "hold_entry"])
    assert "no trades" in seat_report(empty)
    assert "few trades" in conf_monotonicity(empty)
    assert "not recorded" in hold_entry_report(empty) or "disarmed" in hold_entry_report(empty)
