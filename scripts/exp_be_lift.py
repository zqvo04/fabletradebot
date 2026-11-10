"""BE-1R stop lift (STOP_REDESIGN §15): once peak_r >= be_arm_r, lift stop to
entry + be_lock_r*R0. Owner rule = arm 1.0 / lock 0.0. Other cells informational."""
import sys
from dataclasses import replace

sys.path.insert(0, "scripts")
from exp_stop_common import BASE, load, matched, row, show  # noqa: E402

CELLS = [("OWNER 1.0/0.0", 1.0, 0.0), ("1.0/-0.5", 1.0, -0.5),
         ("1.0/+0.1", 1.0, 0.1), ("2.0/0.0", 2.0, 0.0)]

if __name__ == "__main__":
    prep = load()
    b = row(prep, BASE); show("BASELINE", b)
    for lab, arm, lock in CELLS:
        r = row(prep, replace(BASE, be_arm_r=arm, be_lock_r=lock)); show(lab, r)
        tr = r["trades"]
        print(" " * 16, matched(b["trades"], tr))
        if lab.startswith("OWNER"):
            print(tr.groupby("reason")["r"].agg(["size", "mean", "sum"]).round(3))
            print(tr.groupby("setup")["r"].agg(["size", "mean", "sum"]).round(3))
    show("BASE cost2", row(prep, replace(BASE, cost_mult=2.0)))
    show("OWNER cost2", row(prep, replace(BASE, be_arm_r=1.0, cost_mult=2.0)))
