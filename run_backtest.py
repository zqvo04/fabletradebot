"""Demo: run the strategy end-to-end on synthetic regime-switching data.

Usage: python3 run_backtest.py [n_bars] [seed]
"""
import sys

from fabletradebot import Backtester, Config
from fabletradebot.synthetic import generate_market

if __name__ == "__main__":
    n_bars = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    data, funding = generate_market(n_bars=n_bars, seed=seed)
    results = Backtester(data, Config(), funding=funding).run()
    print(results.summary())
