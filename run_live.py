"""Hourly paper-trading step — deterministic replay from LIVE_ANCHOR.

Each run: (1) incrementally extend the CSV cache, (2) replay the engine from
the anchor (data is the only state, so a duplicate or missed cron firing can
never corrupt anything), (3) diff the replay against journal/v1_state.json to
find trades opened/closed since the last run, (4) push Telegram/Notion for the
diff only, (5) save state + print the scoring report.

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
from fabletradebot.config import UNIVERSE, Params
from fabletradebot.data_okx import update_cache
from fabletradebot.engine import run as engine_run
from fabletradebot.scoring import score_report

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

    p = Params()
    frames, funding = load_universe(DATA_DIR)
    if "BTC" not in frames:
        raise SystemExit("no BTC data — cannot classify regime")
    features, candidates, regime_h, corr = prepare(frames, funding, p)
    res = engine_run(frames, features, candidates, funding, regime_h, corr, p,
                     start=pd.Timestamp(ANCHOR, tz="UTC"), equity0=EQUITY0)
    trades, open_pos = res["trades"], res["open_positions"]

    state = load_state()
    known_closed = set(state["closed_keys"])
    known_open = state["open"]

    for _, tr in trades.iterrows():
        key = trade_key(tr["sym"], tr["opened"])
        if key in known_closed:
            continue
        page_id = known_open.pop(key, {}).get("page_id")
        notify.send(notify.fmt_exit(tr.to_dict()))
        journal_notion.post_close(tr.to_dict(), page_id)
        known_closed.add(key)
        print(f"  CLOSE {key} {tr['reason']} {tr['r']:+.2f}R")

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
        notify.send(notify.fmt_entry(info))
        page_id = journal_notion.post_open(info)
        known_open[key] = {"page_id": page_id, "sym": sym,
                           "opened": str(pos.opened_ts)}
        print(f"  OPEN {key} {pos.setup} conf={pos.conf:.2f} {pos.leverage:.0f}x")

    # drop stale open entries whose trades were never seen closing (safety)
    known_open = {k: v for k, v in known_open.items()
                  if k in {trade_key(s, pos.opened_ts) for s, pos in open_pos.items()}}

    state.update({"closed_keys": sorted(known_closed), "open": known_open,
                  "equity": float(eq_now), "anchor": ANCHOR,
                  "last_run": str(pd.Timestamp.now("UTC"))})
    save_state(state)
    print(score_report(trades, res["equity"], EQUITY0))
    print(f"final paper equity: {res['final_equity']:.2f} "
          f"(mtm {eq_now:.2f}), open={list(open_pos)}")


if __name__ == "__main__":
    main()
