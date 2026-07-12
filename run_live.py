"""Hourly paper-trading step — deterministic replay from LIVE_ANCHOR.

Each run: (1) incrementally extend the CSV cache, (2) replay the engine from
the anchor (data is the only state, so a duplicate or missed cron firing can
never corrupt anything), (3) diff the replay against journal/v1_state.json to
find trades opened/closed since the last run, (4) push Telegram/Notion ONLY for
events at/near the latest bar (a freshness guard, so a wiped or hand-edited
state can never resurrect old trades or re-spam Telegram — deep-history events
are absorbed into state silently), (5) mark every STILL-OPEN position to the
latest bar and refresh its
Notion row (hourly scoring runs alongside the trade loop, brief §10), (6) save
state + print the scoring report.

TRADE_MODE=paper is the default and the only mode V1 executes. TRADE_MODE=live
additionally requires OKX keys + LIVE_CONFIRM (see okx_exec.py) and still only
logs intent in V1 — order placement ships after the 4-week paper gate (G8).
"""
from __future__ import annotations

import json
import os

import pandas as pd

from fabletradebot import journal_notion, notify
from fabletradebot.backtest import prepare, load_universe, metrics
from fabletradebot.config import UNIVERSE, Params, profile
from fabletradebot.data_okx import update_cache
from fabletradebot.engine import run as engine_run
from fabletradebot.scoring import mark_to_market, open_report, score_report

DATA_DIR = os.environ.get("LIVE_DATA_DIR", "live_data")
STATE_PATH = "journal/v1_state.json"
ANCHOR = os.environ.get("LIVE_ANCHOR", "2026-07-12")
EQUITY0 = float(os.environ.get("PAPER_EQUITY0", "10000"))


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as fh:
            return json.load(fh)
    return {"closed_keys": [], "open": {}}


def save_state(state: dict) -> None:
    os.makedirs("journal", exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=1, default=str)


def trade_key(sym: str, opened) -> str:
    return f"{sym}|{opened}"


def main() -> None:
    mode = os.environ.get("TRADE_MODE", "paper")
    print(f"== V1 run | mode={mode} | anchor={ANCHOR} ==")
    for sym in UNIVERSE:
        try:
            n_c, n_f = update_cache(sym, DATA_DIR)
            print(f"  {sym}: +{n_c} candles +{n_f} fundings")
        except Exception as exc:
            print(f"  {sym}: update FAILED ({exc}) — replay continues on cache")

    p = profile(os.environ.get("PROFILE", "base"))
    print(f"   leverage profile: {os.environ.get('PROFILE', 'base')}")
    frames, funding = load_universe(DATA_DIR)
    if "BTC" not in frames:
        raise SystemExit("no BTC data — cannot classify regime")
    features, candidates, regime_h, corr = prepare(frames, funding, p)
    res = engine_run(frames, features, candidates, funding, regime_h, corr, p,
                     start=pd.Timestamp(ANCHOR, tz="UTC"), equity0=EQUITY0)
    trades, open_pos = res["trades"], res["open_positions"]

    # Freshness guard: only NOTIFY/JOURNAL events at (or near) the latest bar.
    # The engine replays the whole window from the anchor every run, so without
    # this a wiped/edited state file would re-fire every historical trade to
    # Telegram and re-create rows a user had deleted. Deep-history events are
    # instead absorbed into the state silently (recorded, never re-announced),
    # which also means state loss can at most double-fire the last few hours.
    latest_bar = max(df.index.max() for df in frames.values())
    fresh_h = float(os.environ.get("NOTIFY_FRESH_HOURS", "12"))
    fresh_cut = latest_bar - pd.Timedelta(hours=fresh_h)

    def is_fresh(ts) -> bool:
        return pd.Timestamp(ts) >= fresh_cut

    state = load_state()
    known_closed = set(state["closed_keys"])
    known_open = state["open"]

    for _, tr in trades.iterrows():
        key = trade_key(tr["sym"], tr["opened"])
        if key in known_closed:
            continue
        page_id = known_open.pop(key, {}).get("page_id")
        # announce only recent closes; older ones are silently marked done
        if is_fresh(tr["closed"]):
            notify.send(notify.fmt_exit(tr.to_dict()))
            journal_notion.post_close(tr.to_dict(), page_id)
            print(f"  CLOSE {key} {tr['reason']} {tr['r']:+.2f}R")
        else:
            print(f"  close {key} absorbed (stale replay history)")
        known_closed.add(key)

    eq_now = res["equity"].iloc[-1] if len(res["equity"]) else EQUITY0
    for sym, pos in open_pos.items():
        key = trade_key(sym, pos.opened_ts)
        if key in known_open:
            continue
        info = {"sym": sym, "dir": pos.direction, "conf": pos.conf,
                "setup": pos.setup, "regime": pos.regime, "entry": pos.entry,
                "sl": pos.sl0, "tp1": pos.tp1, "leverage": pos.leverage,
                "risk_amt": pos.risk_amt, "risk_pct": pos.risk_amt / eq_now * 100,
                "equity": eq_now, "opened": pos.opened_ts}
        # announce + create a Notion row only for genuinely new opens; a
        # position first seen already deep in the replay is tracked silently
        # (page_id None -> no row created, no resurrection of deleted rows)
        if is_fresh(pos.opened_ts):
            notify.send(notify.fmt_entry(info))
            page_id = journal_notion.post_open(info)
            print(f"  OPEN {key} {pos.setup} conf={pos.conf:.2f} {pos.leverage:.0f}x")
        else:
            page_id = None
            print(f"  open {key} tracked silently (stale replay history)")
        known_open[key] = {"page_id": page_id, "sym": sym,
                           "opened": str(pos.opened_ts)}

    # drop stale open entries whose trades were never seen closing (safety)
    known_open = {k: v for k, v in known_open.items()
                  if k in {trade_key(s, pos.opened_ts) for s, pos in open_pos.items()}}

    # ---- hourly scoring of positions still OPEN in Notion (brief §10) ----
    # every run, mark each open position to the latest bar and refresh its
    # Notion row (unrealized R / PnL% / hold hours / current stop).
    prices_now = {s: float(frames[s]["close"].iloc[-1]) for s in open_pos}
    for sym, pos in open_pos.items():
        key = trade_key(sym, pos.opened_ts)
        page_id = known_open.get(key, {}).get("page_id")
        if page_id is None:
            continue
        mtm = mark_to_market(pos, prices_now[sym])
        if journal_notion.update_open(page_id, mtm):
            print(f"  SCORE {key} {mtm['r']:+.2f}R held {mtm['bars']}h (open row updated)")

    state.update({"closed_keys": sorted(known_closed), "open": known_open,
                  "equity": float(eq_now), "anchor": ANCHOR,
                  "last_run": str(pd.Timestamp.now("UTC"))})
    save_state(state)
    print(open_report(open_pos, prices_now))
    print(score_report(trades, res["equity"], EQUITY0))
    print(f"final paper equity: {res['final_equity']:.2f} "
          f"(mtm {eq_now:.2f}), open={list(open_pos)}")


if __name__ == "__main__":
    main()
