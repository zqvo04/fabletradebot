"""Offline signal-scoring report: replay v3 & v4 over the full history and
grade every fired signal Win / Loss / Timeout-Win / Timeout-Loss.

The TP/SL/timeout parameters are an UNFITTED diagnostic overlay (see
scoring.py) — this measures signal quality, it does not change trading.

Usage: python3 score_history.py [start] [end]
"""
import sys

import pandas as pd

from fabletradebot.v3 import V3Backtester, v3_config, v4_config, sleeve_signals
from fabletradebot.data_okx import load_market
from fabletradebot.preprocess import resample_ohlcv
from fabletradebot.scoring import simulate_scoring, summarize, OPEN


def report(name, cfg, data, funding):
    res = V3Backtester(data, cfg, funding=funding).run()
    sigs = sleeve_signals(data, cfg)
    positions = simulate_scoring(res.weights, data, sigs, res.equity, cfg, name)
    s = summarize(positions)
    print(f"\n== {name} — signal scoring (TP {cfg.score_tp_k}σ / SL {cfg.score_sl_k}σ / "
          f"timeout {cfg.score_timeout_days:.0f}d) ==")
    if not s.get("n"):
        print("  no resolved positions"); return
    c = s["counts"]
    print(f"  resolved {s['n']}  (still open {s['open']})")
    print(f"  win rate {s['win_rate']:.1%}   avg R {s['avg_r']:+.3f}   sum R {s['sum_r']:+.1f}")
    print(f"  Win {c['Win']}  Loss {c['Loss']}  "
          f"Timeout-Win {c['Timeout-Win']}  Timeout-Loss {c['Timeout-Loss']}")
    by_asset = {}
    for p in positions:
        if p["status"] == OPEN:
            continue
        d = by_asset.setdefault(p["asset"], [0, 0.0])
        d[0] += 1
        d[1] += p["result_r"]
    print("  by asset (n / sumR): " +
          "  ".join(f"{a} {v[0]}/{v[1]:+.1f}" for a, v in sorted(by_asset.items())))


def main(start="2025-01-01", end="2026-07-08"):
    data, funding = load_market(start, end)
    d4 = {a: resample_ohlcv(df) for a, df in data.items()}
    print(f"period {start} .. {end}")
    report("v3", v3_config(), d4, funding)
    report("v4", v4_config(), d4, funding)


if __name__ == "__main__":
    main(*sys.argv[1:3])
