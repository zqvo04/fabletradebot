"""Design-period experiment runner (E-series). ALL results — including failures —
get pasted into EXPERIMENTS.md. Never run this on the holdout window.

Usage: python3 scripts/exp_design.py [exp_id ...]
"""
import sys
from dataclasses import replace

sys.path.insert(0, ".")

import pandas as pd

from fabletradebot.backtest import breakdown, run_backtest
from fabletradebot.config import Params

DESIGN = dict(start="2023-06-01", end="2026-01-31")
DATA = "data"


def show(tag: str, res: dict) -> None:
    m = res["metrics"]
    print(f"\n### {tag}")
    print(", ".join(f"{k}={v}" for k, v in m.items()))
    t = res["trades"]
    if len(t):
        for by in ("setup", "regime"):
            print(breakdown(t, by).to_string())


def conf_calibration(res: dict) -> None:
    t = res["trades"]
    if not len(t):
        return
    t = t.copy()
    t["bucket"] = pd.cut(t["conf"], [0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.0])
    g = t.groupby(["setup", "bucket"], observed=True)
    print(pd.DataFrame({"n": g.size(), "avg_r": g["r"].mean().round(3),
                        "win": g.apply(lambda x: (x.pnl > 0).mean(),
                                       include_groups=False).round(3)}).to_string())


def e1_baseline():
    res = run_backtest(DATA, Params(), **DESIGN)
    show("E1 baseline (all signals, default params)", res)
    print("\nconfidence calibration:")
    conf_calibration(res)


def _only(setups: set[str]) -> Params:
    # disable others by making them unreachable
    kw = {}
    if "CAPREV" not in setups:
        kw["cap_rsi"] = -1.0      # rsi below -1 impossible
    if "BRK" not in setups:
        kw["brk_vol_mult"] = 1e9
    return replace(Params(), **kw)


def e2_isolation():
    for s in ("CAPREV", "BRK"):
        res = run_backtest(DATA, _only({s}), **DESIGN)
        show(f"E2 {s} only", res)


def e3_exits():
    res = run_backtest(DATA, replace(Params(), tp1_frac=1.0), **DESIGN)
    show("E3a full exit at TP1 (fixed 1.5R, no runner)", res)
    res = run_backtest(DATA, replace(Params(), tp1_r=2.5, tp1_frac=1.0), **DESIGN)
    show("E3b full exit at fixed 2.5R", res)
    res = run_backtest(DATA, replace(Params(), trail_atr=2.0), **DESIGN)
    show("E3c tighter trail 2.0 ATR", res)


def e4_conf_threshold():
    for th in (0.55, 0.65, 0.70):
        res = run_backtest(DATA, replace(Params(), conf_entry=th), **DESIGN)
        show(f"E4 conf_entry={th}", res)


EXPS = {"e1": e1_baseline, "e2": e2_isolation, "e3": e3_exits, "e4": e4_conf_threshold}

if __name__ == "__main__":
    for name in (sys.argv[1:] or ["e1"]):
        EXPS[name]()
