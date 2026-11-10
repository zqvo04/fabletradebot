"""T6 (STOP_REDESIGN §12): engine measurement of the two pre-registered loss-side
cuts. Primary cells fixed before this run: L1 loss_cut_r=0.7 (no ema leg);
L2 loss_cut_r=0.5 + loss_cut_ema=0.5. Neighbours are flatness checks only —
argmax adoption is forbidden. Gate G5 (full window AND both halves).

Both candidates were REJECTED, so the engine flag was NOT committed: this script
needs the temporary loss_cut_r/loss_cut_ema patch reproduced in STOP_REDESIGN §12f."""
import sys

sys.path.insert(0, "scripts")

from dataclasses import replace

from exp_stop_common import BASE, load, row, show

if __name__ == "__main__":
    prep = load()
    cells = [("BASELINE", BASE),
             ("L1 0.7 *", replace(BASE, loss_cut_r=0.7)),
             ("L1 0.6", replace(BASE, loss_cut_r=0.6)),
             ("L1 0.8", replace(BASE, loss_cut_r=0.8)),
             ("L2 .5/.5 *", replace(BASE, loss_cut_r=0.5, loss_cut_ema=0.5)),
             ("L2 .4/.5", replace(BASE, loss_cut_r=0.4, loss_cut_ema=0.5)),
             ("L2 .6/.5", replace(BASE, loss_cut_r=0.6, loss_cut_ema=0.5)),
             ("L2 .5/.25", replace(BASE, loss_cut_r=0.5, loss_cut_ema=0.25)),
             ("L2 .5/1.0", replace(BASE, loss_cut_r=0.5, loss_cut_ema=1.0)),
             ("BASE c2", replace(BASE, cost_mult=2.0)),
             ("L1 0.7 c2", replace(BASE, loss_cut_r=0.7, cost_mult=2.0)),
             ("L2 .5/.5 c2", replace(BASE, loss_cut_r=0.5, loss_cut_ema=0.5, cost_mult=2.0))]
    for label, p in cells:
        r = row(prep, p)
        show(label, r)
        g = r["trades"].groupby("reason")["r"].agg(["size", "mean"]).round(3)
        print(" " * 16, {k: (int(v["size"]), v["mean"]) for k, v in g.iterrows()})
