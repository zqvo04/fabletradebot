"""4H paper/live loop for the v3 continuous portfolio system — deterministic
replay design (same principle as run_live.py: the market data IS the state).

Every run replays the v3 backtest from a fixed anchor over cached +
incrementally-updated OKX data, journals the latest target weights, and (in
live mode) reconciles the real book to them with market-order deltas.

Env:
  TRADE_MODE          paper (default) | live
  LIVE_ANCHOR         replay start date (default 2026-01-01; needs ~35d warmup)
  PAPER_EQUITY0       starting equity   (default 100000)
  OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE / LIVE_CONFIRM=YES  live orders
"""
import json
import os
from pathlib import Path

import pandas as pd

from fabletradebot.v3 import V3Backtester, v3_config
from fabletradebot.data_okx import update_market
from fabletradebot.preprocess import resample_ohlcv

JOURNAL = Path("journal")
WEIGHTS_CSV = JOURNAL / "v3_weights.csv"
STATE_JSON = JOURNAL / "v3_state.json"


def main():
    anchor = os.environ.get("LIVE_ANCHOR", "2026-01-01")
    equity0 = float(os.environ.get("PAPER_EQUITY0", "100000"))
    mode = os.environ.get("TRADE_MODE", "paper")
    JOURNAL.mkdir(exist_ok=True)

    data, funding = update_market(anchor, cache_dir="live_data")
    anchor_ts = pd.Timestamp(anchor, tz="UTC")
    data = {a: resample_ohlcv(df.loc[df.index >= anchor_ts]) for a, df in data.items()}
    out = V3Backtester(data, v3_config(), funding=funding, equity0=equity0).run()

    state = json.loads(STATE_JSON.read_text()) if STATE_JSON.exists() else {"n_journaled": 0}
    n_old = int(state.get("n_journaled", 0))
    w = out.weights.round(6)
    new = w.iloc[n_old:]
    if len(new):
        new.to_csv(WEIGHTS_CSV, mode="a", header=not WEIGHTS_CSV.exists())

    equity = float(out.equity.iloc[-1])
    target = {a: float(w[a].iloc[-1]) for a in w.columns}
    marks = {a: float(df["close"].iloc[-1]) for a, df in data.items()}
    target_qty = {a: target[a] * equity / marks[a] for a in target}

    if mode == "live":
        _reconcile(state.get("live_qty", {}), target_qty)

    state.update(
        n_journaled=len(w),
        equity=equity,
        last_bar=str(out.equity.index[-1]),
        target_weights=target,
        live_qty=target_qty,
        mode=mode,
    )
    STATE_JSON.write_text(json.dumps(state, indent=2))
    print(f"[v3/{mode}] bar {state['last_bar']}  equity {equity:.2f}  "
          f"weights { {a: round(v, 3) for a, v in target.items()} }")


def _reconcile(prev: dict, target: dict):
    """LIVE mode: market-order the per-asset qty delta. Dry-runs unless the
    OKX adapter is fully armed (see okx_exec)."""
    from fabletradebot.okx_exec import place_market_order
    for a in set(prev) | set(target):
        delta = float(target.get(a, 0.0)) - float(prev.get(a, 0.0))
        if abs(delta) < 1e-9:
            continue
        place_market_order(a, "buy" if delta > 0 else "sell", abs(delta))


if __name__ == "__main__":
    main()
