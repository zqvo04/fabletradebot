"""Hourly paper-trading step — INCREMENTAL live replay (rolling anchor).

Each run advances a persisted anchor: it replays only the bars that are NEW
since the last run, seeded with the prior run's carried engine state (open
positions, equity, cooldowns, pending fills, circuit/DD governor). The updated
carry + a rolled-forward anchor are saved back to journal/v1_state.json.

Why this shape (the resurrection fix): the old loop replayed a FROZEN anchor
over an ever-growing window every run, so any still-open position was re-derived
from the data on every run — clearing the state file could never stick, and a
wiped state re-emitted history. Now the anchor is the single high-water mark:
once a bar is processed the anchor moves past it, so nothing before the anchor
is ever re-derived or re-announced. The chunked replay is proven identical to a
single full pass (tests/test_infra.py), so this changes persistence, not the
trading rules. A lost/old-schema state is a clean reset — the system simply
starts fresh from the latest real-time bar (never from ancient history).

TRADE_MODE=paper is the default. LIVE_RESET=1 forces a fresh start from now;
LIVE_ANCHOR (optional) sets an explicit fresh-start bar instead of the latest.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from fabletradebot import journal_notion, notify
from fabletradebot.backtest import load_universe, prepare
from fabletradebot.config import UNIVERSE, profile
from fabletradebot.data_okx import update_cache
from fabletradebot.engine import deserialize_carry, serialize_carry
from fabletradebot.engine import run as engine_run
from fabletradebot.scoring import mark_to_market, open_report, score_report

DATA_DIR = os.environ.get("LIVE_DATA_DIR", "live_data")
STATE_PATH = "journal/v1_state.json"
EQUITY0 = float(os.environ.get("PAPER_EQUITY0", "10000"))
SCHEMA = 2   # incremental-carry state; anything else is treated as a fresh reset


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as fh:
            try:
                return json.load(fh)
            except json.JSONDecodeError as exc:
                # a corrupted/partial write (e.g. an interrupted commit) must
                # never crash the loop — treat it exactly like a missing file:
                # schema check below sees no "schema" key and triggers a clean
                # reset from the latest real-time bar (never ancient history)
                print(f"  state file unreadable ({exc}) — treating as a reset")
                return {}
    return {}


def save_state(state: dict) -> None:
    os.makedirs("journal", exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=1, default=str)


def trade_key(sym: str, opened) -> str:
    return f"{sym}|{opened}"


def main() -> None:
    mode = os.environ.get("TRADE_MODE", "paper")
    for sym in UNIVERSE:
        try:
            n_c, n_f = update_cache(sym, DATA_DIR)
            print(f"  {sym}: +{n_c} candles +{n_f} fundings")
        except Exception as exc:
            print(f"  {sym}: update FAILED ({exc}) — replay continues on cache")

    p = profile(os.environ.get("PROFILE", "whale"))
    frames, funding = load_universe(DATA_DIR)
    if "BTC" not in frames:
        raise SystemExit("no BTC data — cannot classify regime")
    features, candidates, regime_h, corr = prepare(frames, funding, p)
    latest_bar = max(df.index.max() for df in frames.values())

    # ---- resolve the replay window from persisted state ----
    state = load_state()
    reset = os.environ.get("LIVE_RESET") == "1" or state.get("schema") != SCHEMA
    if reset:
        # fresh start: begin from the latest real-time bar (NOT ancient history),
        # or an explicit LIVE_ANCHOR if the operator set one. No carry, no pages.
        anchor_env = os.environ.get("LIVE_ANCHOR")
        start = pd.Timestamp(anchor_env, tz="UTC") if anchor_env else latest_bar
        carry, pages, closed_list = None, {}, []
    else:
        start = pd.Timestamp(state["anchor"])
        carry = deserialize_carry(state["carry"]) if state.get("carry") else None
        pages = dict(state.get("pages", {}))
        closed_list = list(state.get("closed_keys", []))   # chronological order
    closed_keys = set(closed_list)

    print(f"== V1 run | mode={mode} | profile={os.environ.get('PROFILE', 'whale')} | "
          f"{'RESET ' if reset else ''}anchor={start} | latest={latest_bar} ==")

    res = engine_run(frames, features, candidates, funding, regime_h, corr, p,
                     start=start, equity0=EQUITY0, carry=carry)
    trades, open_pos = res["trades"], res["open_positions"]
    # mark-to-market equity (cash + unrealized), NOT res["final_equity"] (cash
    # only) — whale mode concentrates the whole account in one position, so
    # its unrealized swing is exactly what must be reflected here. Empty curve
    # means no new bar existed this run (e.g. a cron tick before OKX published
    # the next candle) — carry forward the last known equity unchanged.
    eq_now = (float(res["equity"].iloc[-1]) if len(res["equity"])
             else float(state.get("equity", EQUITY0)) if not reset else EQUITY0)

    # Every event this run sits on a bar >= anchor, i.e. genuinely new — no
    # wall-clock freshness heuristic needed. closed_keys / pages only guard the
    # rare case of a run repeating before its state commit landed.
    for _, tr in trades.iterrows():
        key = trade_key(tr["sym"], tr["opened"])
        if key in closed_keys:
            continue
        page_id = pages.pop(key, None)
        notify.send(notify.fmt_exit(tr.to_dict()))
        journal_notion.post_close(tr.to_dict(), page_id)
        closed_keys.add(key)
        closed_list.append(key)   # append-order == chronological (trades are time-sorted)
        print(f"  CLOSE {key} {tr['reason']} {tr['r']:+.2f}R")

    for sym, pos in open_pos.items():
        key = trade_key(sym, pos.opened_ts)
        if key in pages:            # carried from a prior run — already journaled
            continue
        info = {"sym": sym, "dir": pos.direction, "conf": pos.conf,
                "setup": pos.setup, "regime": pos.regime, "entry": pos.entry,
                "sl": pos.sl0, "tp1": pos.tp1, "leverage": pos.leverage,
                "risk_amt": pos.risk_amt, "risk_pct": pos.risk_amt / eq_now * 100,
                "equity": eq_now, "opened": pos.opened_ts}
        notify.send(notify.fmt_entry(info))
        pages[key] = journal_notion.post_open(info)
        print(f"  OPEN {key} {pos.setup} conf={pos.conf:.2f} {pos.leverage:.0f}x")

    # ---- hourly scoring of positions still OPEN (brief §10) ----
    prices_now = {s: float(frames[s]["close"].iloc[-1]) for s in open_pos}
    for sym, pos in open_pos.items():
        key = trade_key(sym, pos.opened_ts)
        page_id = pages.get(key)
        if page_id is None:
            continue
        mtm = mark_to_market(pos, prices_now[sym])
        if journal_notion.update_open(page_id, mtm):
            print(f"  SCORE {key} {mtm['r']:+.2f}R held {mtm['bars']}h "
                  f"hold_conf {mtm['hold_conf']:.2f}")

    # ---- persist: roll the anchor past the newest processed bar ----
    save_state({
        "schema": SCHEMA,
        "anchor": str(latest_bar + pd.Timedelta(hours=1)),
        "last_bar": str(latest_bar),
        "equity": float(eq_now),
        "carry": serialize_carry(res["carry"]),
        "pages": pages,
        "closed_keys": closed_list[-500:],   # dedup guard only — chronologically pruned
        "last_run": str(pd.Timestamp.now("UTC")),
    })
    print(open_report(open_pos, prices_now))
    print(score_report(trades, res["equity"], EQUITY0))
    print(f"paper equity: {eq_now:.2f} | open={list(open_pos)} | "
          f"next anchor={latest_bar + pd.Timedelta(hours=1)}")


if __name__ == "__main__":
    main()
