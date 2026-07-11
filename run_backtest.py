"""CLI backtest: python3 run_backtest.py [start] [end] [data_dir]"""
import sys

from fabletradebot.backtest import run_backtest
from fabletradebot.scoring import score_report

if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2023-06-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-01-31"
    data_dir = sys.argv[3] if len(sys.argv) > 3 else "data"
    res = run_backtest(data_dir, start=start, end=end)
    print(score_report(res["trades"], res["equity"], 10_000.0))
