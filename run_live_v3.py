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

from fabletradebot.v3 import (V3Backtester, v3_config, v4_config, v5_config,
                              sleeve_signals)
from fabletradebot.data_okx import INSTRUMENTS, update_market
from fabletradebot.leverage_plan import plan_leverage, format_plan
from fabletradebot.preprocess import resample_ohlcv
from fabletradebot.okx_account import fetch_equity
from fabletradebot.journal_notion import post_scored, update_scored
from fabletradebot.notify import send_telegram, format_scored_open, format_scored_close
from fabletradebot.scoring import simulate_scoring, summarize, OPEN

# V3_PROFILE selects the risk profile: "v3" (base, vol budget 0.2), "v4"
# (aggressive, 0.4 + liquidation stress guard) or "v5" (v4 + maker execution,
# satellite universe, portfolio vol cap). Each profile keeps its own journal
# so forward tracks stay comparable.
PROFILE = os.environ.get("V3_PROFILE", "v3").lower()
CONFIGS = {"v3": v3_config, "v4": v4_config, "v5": v5_config}

JOURNAL = Path("journal")
WEIGHTS_CSV = JOURNAL / f"{PROFILE}_weights.csv"
STATE_JSON = JOURNAL / f"{PROFILE}_state.json"

# cap Notion writes per run so a first run over a long history can't hit rate
# limits; the reconcile is idempotent, so any backlog clears on later runs.
MAX_NOTION_WRITES = int(os.environ.get("MAX_NOTION_WRITES", "40"))


def _fresh(ts: str | None, now: pd.Timestamp, days: float = 1.5) -> bool:
    """True if an event is recent enough to push a Telegram alert — keeps the
    initial history backfill from spamming notifications for old trades."""
    if not ts:
        return False
    return pd.Timestamp(ts) >= now - pd.Timedelta(days=days)


def reconcile_scored(positions: list[dict], scored_state: dict,
                     now: pd.Timestamp) -> int:
    """Create Notion rows for newly seen positions and patch ones that just
    resolved. `scored_state` maps position id -> {page_id, status}; mutated
    in place. Returns the number of Notion writes performed."""
    writes = 0
    for pos in positions:
        if writes >= MAX_NOTION_WRITES:
            break
        known = scored_state.get(pos["id"])
        if known is None:                       # first sighting -> create
            page_id = post_scored(pos)
            writes += 1
            if page_id:
                scored_state[pos["id"]] = {"page_id": page_id, "status": pos["status"]}
                if pos["status"] == OPEN and _fresh(pos["opened_ts"], now):
                    send_telegram(format_scored_open(pos))
                elif pos["status"] != OPEN and _fresh(pos["closed_ts"], now):
                    send_telegram(format_scored_close(pos))
        elif known["status"] == OPEN and pos["status"] != OPEN:   # just resolved
            if update_scored(known["page_id"], pos):
                writes += 1
                known["status"] = pos["status"]
                if _fresh(pos["closed_ts"], now):
                    send_telegram(format_scored_close(pos))
    return writes


def main():
    anchor = os.environ.get("LIVE_ANCHOR", "2026-01-01")
    equity0 = float(os.environ.get("PAPER_EQUITY0", "100000"))
    mode = os.environ.get("TRADE_MODE", "paper")
    JOURNAL.mkdir(exist_ok=True)

    cfg = CONFIGS.get(PROFILE, v3_config)()
    assets = dict(INSTRUMENTS)
    assets.update({a: f"{a}-USDT-SWAP" for a in cfg.satellites})
    data, funding = update_market(anchor, cache_dir="live_data", assets=assets)
    anchor_ts = pd.Timestamp(anchor, tz="UTC")
    data = {a: resample_ohlcv(df.loc[df.index >= anchor_ts]) for a, df in data.items()}
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

    # --- scored signals: open positions with TP/SL, grade Win/Loss/Timeout ---
    # Stateless recompute over the full history, then reconcile with Notion
    # (idempotent, matching the deterministic-replay design of the whole loop).
    sigs = sleeve_signals(data, cfg, funding=funding)
    positions = simulate_scoring(out.weights, data, sigs, out.equity, cfg, PROFILE)
    scored_state = state.get("scored", {})
    writes = reconcile_scored(positions, scored_state, out.equity.index[-1])
    stats = summarize(positions)

    # --- confidence-tiered account leverage plan (hard stops, never sizing) ---
    dd = float(out.equity.iloc[-1] / out.equity.cummax().iloc[-1] - 1.0)
    sig_last = {a: {k: float(sigs[a][k].iloc[-1]) for k in
                    ("xs", "agree", "disp_pct", "vol_ann")} for a in target}
    lev_plan = plan_leverage(target, sig_last, dd, cfg)

    if mode == "live":
        _set_account_tiers(lev_plan)
        _reconcile(state.get("live_qty", {}), target_qty)

    state.update(
        n_journaled=len(w),
        equity=equity,
        okx_equity=okx_equity,
        last_bar=bar_time,
        target_weights=target,
        scored=scored_state,
        live_qty=target_qty,
        leverage_plan=lev_plan,
        mode=mode,
    )
    STATE_JSON.write_text(json.dumps(state, indent=2))
    wr = f"{stats['win_rate']:.0%}" if stats.get("n") else "n/a"
    live_w = {a: round(v, 3) for a, v in target.items() if abs(v) > 1e-6}
    print(f"[{PROFILE}/{mode}] bar {bar_time}  equity {equity:.2f}"
          f"{' (okx)' if okx_equity is not None else ' (paper)'}  "
          f"weights {live_w if live_w else '(flat)'}")
    print(f"[{PROFILE}/scored] resolved {stats.get('n', 0)} (win {wr}, "
          f"avgR {stats.get('avg_r', 0):+.2f})  open {stats.get('open', 0)}  "
          f"notion writes {writes}")
    if lev_plan["assets"]:
        print(f"[{PROFILE}/tiers]\n{format_plan(lev_plan)}")


def _set_account_tiers(plan: dict):
    """LIVE mode: push each open position's confidence tier to the exchange
    as the account-level hard stop above the software ceilings. Never touches
    position sizes. Dry-runs unless the OKX adapter is fully armed."""
    from fabletradebot.okx_exec import set_leverage
    for a, p in plan["assets"].items():
        set_leverage(a, p["tier"])


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
