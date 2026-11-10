"""S3 stop-floor sweep (STOP_REDESIGN.md §2 S3, task T1). Config only, no source change.

sl_floor_atr is applied at candidate stage (signals.scan), so each cell re-runs
prepare(). Grid {2.0 baseline, 2.5 PRIMARY (pre-registered), 3.0, 4.0}; the
primary cell and baseline are also run at cost_mult=2.0 (G6). Adopting the argmax
is forbidden; the gate is G5 (ret / MDD / avg_r not worse than baseline on full
window AND both halves) applied to the primary cell only.

Usage (repo root): python3 scripts/exp_stop_floor.py
"""
import sys
from dataclasses import replace

sys.path.insert(0, "scripts")

from exp_stop_common import BASE, load, row, show  # noqa: E402

CELLS = [
    ("floor2.0", replace(BASE, sl_floor_atr=2.0)),
    ("floor2.5*", replace(BASE, sl_floor_atr=2.5)),
    ("floor3.0", replace(BASE, sl_floor_atr=3.0)),
    ("floor4.0", replace(BASE, sl_floor_atr=4.0)),
    ("floor2.0 c2", replace(BASE, sl_floor_atr=2.0, cost_mult=2.0)),
    ("floor2.5* c2", replace(BASE, sl_floor_atr=2.5, cost_mult=2.0)),
]

for label, p in CELLS:
    prep = load(p)
    r = row(prep, p)
    show(label, r)
    t = r["trades"]
    sl = t[t["reason"] == "SL"]
    print(f"{'':>14}   SL n {len(sl)}  SL mean R {sl['r'].mean():.3f}  "
          f"mean leverage {t['leverage'].mean():.3f}", flush=True)
