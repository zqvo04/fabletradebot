"""Backfill / update the OKX CSV cache for the whole universe.

Usage: python3 fetch_data.py [data_dir]   (default: data)
"""
import sys

from fabletradebot.config import UNIVERSE
from fabletradebot.data_okx import update_cache


def main(data_dir: str = "data") -> None:
    for sym in UNIVERSE:
        try:
            n_c, n_f = update_cache(sym, data_dir)
            print(f"{sym}: +{n_c} candles, +{n_f} fundings", flush=True)
        except Exception as exc:
            print(f"{sym}: FAILED — {exc}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
