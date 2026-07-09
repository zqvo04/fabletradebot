"""Hourly paper/live trading loop — deterministic replay design.

Every run replays the full backtest from a FIXED anchor date over cached +
incrementally-updated OKX data. Because the replay is deterministic, its trade
list is append-only: anything beyond the last journaled trade is new. This
gives crash-safe state (the market data IS the state) with no drift between
runs.

Env:
  TRADE_MODE          paper (default) | live
  LIVE_ANCHOR         replay start date   (default 2026-01-01; needs 90d warmup)
                      NOTE: changing the anchor resets journal continuity —
                      wipe journal/ if you change it.
  PAPER_EQUITY0       starting equity     (default 100000)
  NOTION_TOKEN / NOTION_DATABASE_ID   optional trade journal
  OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE / LIVE_CONFIRM=YES  live orders
"""
import json
import os
from pathlib import Path

import pandas as pd

from fabletradebot import Backtester
from fabletradebot.config import v2_config
from fabletradebot.data_okx import update_market
from fabletradebot.journal_notion import post_trade
from fabletradebot.preprocess import resample_ohlcv

JOURNAL = Path("journal")
TRADES_CSV = JOURNAL / "paper_trades.csv"
STATE_JSON = JOURNAL / "paper_state.json"


def main():
    anchor = os.environ.get("LIVE_ANCHOR", "2026-01-01")
    equity0 = float(os.environ.get("PAPER_EQUITY0", "100000"))
    mode = os.environ.get("TRADE_MODE", "paper")
    JOURNAL.mkdir(exist_ok=True)

    data, funding = update_market(anchor, cache_dir="live_data")
    # deterministic replay: fixed anchor even if the cache holds older bars,
    # resampled to the gate-validated 4H tempo, v2 strategy (VALIDATION_V2.md)
    anchor_ts = pd.Timestamp(anchor, tz="UTC")
    data = {a: resample_ohlcv(df.loc[df.index >= anchor_ts]) for a, df in data.items()}
    bt = Backtester(data, v2_config(), funding=funding, equity0=equity0)
    out = bt.run()

    state = json.loads(STATE_JSON.read_text()) if STATE_JSON.exists() else {"n_journaled": 0}
    n_old = int(state.get("n_journaled", 0))
    trades = out.trades
    new = trades.iloc[n_old:] if len(trades) > n_old else trades.iloc[0:0]

    if len(new):
        header = not TRADES_CSV.exists()
        new.to_csv(TRADES_CSV, mode="a", header=header, index=False)
        for _, t in new.iterrows():
            post_trade(t.to_dict())

    open_positions = {
        a: {"direction": p.direction, "qty": p.qty, "avg_entry": p.avg_entry,
            "stop": p.stop, "playbook": p.playbook}
        for a, p in bt._engine.positions.items() if p is not None
    }

    if mode == "live":
        _reconcile(state.get("live_positions", {}), open_positions)

    state.update(
        n_journaled=len(trades),
        equity=float(out.equity.iloc[-1]),
        last_bar=str(out.equity.index[-1]),
        open_positions=open_positions,
        live_positions={a: p["direction"] * p["qty"] for a, p in open_positions.items()},
        mode=mode,
    )
    STATE_JSON.write_text(json.dumps(state, indent=2))
    print(f"[{mode}] bar {state['last_bar']}  equity {state['equity']:.2f}  "
          f"new trades {len(new)}  open {list(open_positions)}")


def _reconcile(prev: dict, target: dict):
    """LIVE mode: market-order the delta between previous and target net
    position per asset. Dry-runs unless the adapter is fully armed."""
    from fabletradebot.okx_exec import place_market_order
    assets = set(prev) | set(target)
    for a in assets:
        want = target.get(a, {})
        want_net = want.get("direction", 0) * want.get("qty", 0.0)
        delta = want_net - float(prev.get(a, 0.0))
        if abs(delta) < 1e-9:
            continue
        place_market_order(a, "buy" if delta > 0 else "sell", abs(delta))


if __name__ == "__main__":
    main()
