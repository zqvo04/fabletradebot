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

from fabletradebot.v3 import V3Backtester, v3_config, v4_config
from fabletradebot.data_okx import update_market
from fabletradebot.preprocess import resample_ohlcv
from fabletradebot.okx_account import fetch_equity
from fabletradebot.journal_notion import post_signal
from fabletradebot.notify import send_telegram, format_signal

# V3_PROFILE selects the risk profile: "v3" (base, vol budget 0.2) or
# "v4" (aggressive, 0.4 + liquidation stress guard). Same signal either way;
# each profile keeps its own journal so forward tracks stay comparable.
PROFILE = os.environ.get("V3_PROFILE", "v3").lower()

JOURNAL = Path("journal")
WEIGHTS_CSV = JOURNAL / f"{PROFILE}_weights.csv"
STATE_JSON = JOURNAL / f"{PROFILE}_state.json"

# a signal "fires" when a target weight moves at least this much (fraction of
# equity) from the last fired target — the notify/log analogue of a rebalance.
NOTIFY_THRESHOLD = float(os.environ.get("SIGNAL_NOTIFY_THRESHOLD", "0.03"))


def signal_changes(prev: dict, new: dict, threshold: float) -> list[dict]:
    """Assets whose target weight moved >= threshold since the last fire.
    Direction is the sign of the NEW target (the position we now want)."""
    out = []
    for a, nw in new.items():
        pw = float(prev.get(a, 0.0))
        delta = nw - pw
        if abs(delta) >= threshold:
            direction = "LONG" if nw > 1e-9 else "SHORT" if nw < -1e-9 else "FLAT"
            out.append(dict(asset=a, prev_weight=pw, target_weight=nw,
                            delta=delta, direction=direction))
    return out


def main():
    anchor = os.environ.get("LIVE_ANCHOR", "2026-01-01")
    equity0 = float(os.environ.get("PAPER_EQUITY0", "100000"))
    mode = os.environ.get("TRADE_MODE", "paper")
    JOURNAL.mkdir(exist_ok=True)

    data, funding = update_market(anchor, cache_dir="live_data")
    anchor_ts = pd.Timestamp(anchor, tz="UTC")
    data = {a: resample_ohlcv(df.loc[df.index >= anchor_ts]) for a, df in data.items()}
    cfg = v4_config() if PROFILE == "v4" else v3_config()
    out = V3Backtester(data, cfg, funding=funding, equity0=equity0).run()

    state = json.loads(STATE_JSON.read_text()) if STATE_JSON.exists() else {"n_journaled": 0}
    n_old = int(state.get("n_journaled", 0))
    w = out.weights.round(6)
    new = w.iloc[n_old:]
    if len(new):
        new.to_csv(WEIGHTS_CSV, mode="a", header=not WEIGHTS_CSV.exists())

    paper_equity = float(out.equity.iloc[-1])
    okx_equity = fetch_equity()                 # real account equity if 3 keys set
    equity = okx_equity if okx_equity is not None else paper_equity
    bar_time = str(out.equity.index[-1])
    target = {a: float(w[a].iloc[-1]) for a in w.columns}
    marks = {a: float(df["close"].iloc[-1]) for a, df in data.items()}
    target_qty = {a: target[a] * equity / marks[a] for a in target}

    # --- signal firing: notify + journal when the target moved materially ---
    prev_fired = state.get("last_signal_weights", {})
    for sig in signal_changes(prev_fired, target, NOTIFY_THRESHOLD):
        sig.update(system=PROFILE, equity=equity, bar_time=bar_time,
                   note="XS 횡단면 모멘텀 리밸런스")
        post_signal(sig)
        send_telegram(format_signal(sig))
        print(f"[signal] {sig['asset']} {sig['direction']} "
              f"{sig['prev_weight']:+.3f} -> {sig['target_weight']:+.3f}")

    if mode == "live":
        _reconcile(state.get("live_qty", {}), target_qty)

    state.update(
        n_journaled=len(w),
        equity=equity,
        okx_equity=okx_equity,
        last_bar=bar_time,
        target_weights=target,
        last_signal_weights=target,
        live_qty=target_qty,
        mode=mode,
    )
    STATE_JSON.write_text(json.dumps(state, indent=2))
    print(f"[{PROFILE}/{mode}] bar {bar_time}  equity {equity:.2f}"
          f"{' (okx)' if okx_equity is not None else ' (paper)'}  "
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
