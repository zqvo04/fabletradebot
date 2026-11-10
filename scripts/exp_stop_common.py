"""Shared harness for STOP_REDESIGN measurements (whale, design window, half-cross).

row(prep, p) -> dict with terminal ret / MDD / avg_r / pf / payoff_r (RR target, §8)
for the full window and both halves. Same windows as exp_giveback_arm.py.
"""
import sys

sys.path.insert(0, ".")

import pandas as pd

from fabletradebot.backtest import load_universe, metrics, prepare
from fabletradebot.config import profile
from fabletradebot.engine import run as engine_run

DATA = "data"
FULL = (pd.Timestamp("2023-06-01", tz="UTC"), pd.Timestamp("2026-01-31", tz="UTC"))
MID = pd.Timestamp("2024-09-30 12:00", tz="UTC")
BASE = profile("whale")


def load(p=BASE):
    frames, funding = load_universe(DATA)
    features, candidates, states, corr = prepare(frames, funding, p)
    return frames, features, candidates, funding, states, corr


def row(prep, p):
    frames, features, candidates, funding, states, corr = prep

    def m(w):
        res = engine_run(frames, features, candidates, funding, states, corr, p,
                         start=w[0], end=w[1], equity0=10_000.0)
        return metrics(res["trades"], res["equity"], 10_000.0), res["trades"]

    (mf, tr), (m1, _), (m2, _) = m(FULL), m((FULL[0], MID)), m((MID, FULL[1]))
    return {"ret": mf.get("total_return"), "mdd": mf.get("max_dd"),
            "avg_r": mf.get("avg_r"), "pf": mf.get("profit_factor"),
            "payoff": mf.get("payoff_r"), "wr": mf.get("win_rate"), "n": mf.get("trades"),
            "h1_r": m1.get("avg_r"), "h1_ret": m1.get("total_return"),
            "h2_r": m2.get("avg_r"), "h2_ret": m2.get("total_return"), "trades": tr}


def show(label, r):
    print(f"{label:>14} | ret {r['ret']:>9} mdd {r['mdd']:>8} avg_r {r['avg_r']:>7} "
          f"pf {r['pf']:>6} payoff {r['payoff']:>6} wr {r['wr']:>6} n {r['n']:>4} | "
          f"H1 {r['h1_r']:>7}/{r['h1_ret']:>8} | H2 {r['h2_r']:>7}/{r['h2_ret']:>8}")


if __name__ == "__main__":
    import time
    t = time.time(); prep = load(); r = row(prep, BASE); show("BASELINE", r)
    print(r["trades"].groupby("reason")["r"].agg(["size", "mean", "sum"]).round(3))
    print(f"elapsed {time.time() - t:.0f}s")
