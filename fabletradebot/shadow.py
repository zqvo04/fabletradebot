"""Shadow book — a learning-only replay that always has a free seat.

whale holds exactly ONE position, so the live forward track resolves ~12 trades
a month. That is the binding constraint on every entry/exit question the design
docs still have open: E19b needed 975 counterfactual episodes to say anything at
all, and it could only get them from the design window (which may not judge — E12).
This module produces the forward version of that record: the SAME bars through
the SAME engine, with only the seat and the account-level governors lifted, so
each candidate the seat rejected becomes a fully managed virtual trade carrying
its own exit legs, R, PnL and give-back.

What is deliberately NOT changed: cooldown, confidence tiers, CRISIS blocking,
risk_scale/SR-D promotion, and every exit parameter. Those are the entry/exit
logic under study, not the seat — changing them here would produce a record that
cannot be compared with the live one.

Isolation from the live account is the other half of the design:
  - no notifications (nobody is asked to act on a shadow trade),
  - its own Notion DB and its own ledger — the SR-D forward ledger sizes REAL
    positions and must never see a shadow trade,
  - its equity is read by nothing; run_live wraps the call in try/except so a
    bug in here cannot stop the live loop.
"""
from __future__ import annotations

import os

import pandas as pd
from dataclasses import replace

from . import journal_notion
from .config import UNIVERSE, Params
from .engine import deserialize_carry, run, serialize_carry

LEDGER_PATH = "journal/shadow_ledger.csv"
LEDGER_COLS = ["closed", "opened", "sym", "setup", "dir", "seat_state", "r", "pnl",
               "reason", "regime", "conf", "peak_r", "bars", "hold_entry", "leverage"]
EQUITY = float(os.environ.get("SHADOW_EQUITY", "10000"))
TAKEN, BLOCKED = "Live-Taken", "Live-Blocked"


def params(p: Params) -> Params:
    """The live params with ONLY the seat and account-level governors lifted."""
    n = len(UNIVERSE)
    return replace(
        p,
        # always a free seat: the engine still allows one position per symbol,
        # so the whole universe is the ceiling (see `same_symbol_blocked`).
        max_positions=n, max_positions_corr=n,
        # portfolio-level caps must not veto an entry the seat already allowed
        max_open_risk=1e9, max_margin_frac=1e9,
        # account governors would stop DATA COLLECTION here, not risk — the
        # shadow book holds no money. dd_stop/dd_half become unreachable and
        # dd_resume unreachable-from-below, i.e. a freeze can never engage and
        # would release on the next bar if it somehow did.
        dd_stop=1e9, dd_half=1e9, dd_resume=1e9, circuit_loss_24h=1e9,
        # fixed sizing equity — see Params.size_equity: with up to `n` concurrent
        # full-margin positions the shared mtm equity would couple their sizes
        # and can go negative, flipping the sign of R.
        size_equity=EQUITY,
    )


def append_trade(row: dict, seat_state: str, path: str = LEDGER_PATH) -> None:
    rec = {k: row.get(k) for k in LEDGER_COLS}
    rec["seat_state"] = seat_state
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame([rec]).to_csv(path, mode="a", header=not os.path.exists(path),
                               index=False)


def same_symbol_blocked(candidates: dict, positions: dict, bar) -> int:
    """Candidates dropped because the engine allows one position per symbol.

    The always-free seat removes the CROSS-coin bottleneck only; a second signal
    on a coin the shadow book already holds is still discarded. Counting them is
    how we find out whether lifting that too is worth an engine change.
    """
    return sum(1 for sym, df in candidates.items()
               if sym in positions and bar in df.index)


def step(args: tuple, p: Params, start: pd.Timestamp, latest_bar: pd.Timestamp,
         state: dict, live_keys: set[str], trade_key) -> dict:
    """Replay the shadow book over the same window and journal what resolved.

    `state` is the persisted v1_state.json (read-only here); the returned dict is
    merged back into it by the caller. `live_keys` are the "sym|opened" keys the
    REAL book took, which is what splits the record into Live-Taken (the seat's
    own picks) and Live-Blocked (what it rejected) — the E19b axis, forward.
    """
    sp = params(p)
    carry = deserialize_carry(state["shadow_carry"]) if state.get("shadow_carry") else None
    pages = dict(state.get("shadow_pages", {}))
    seats = dict(state.get("shadow_seats", {}))
    closed_list = list(state.get("shadow_closed_keys", []))
    closed_keys = set(closed_list)

    res = run(*args, sp, start=start, equity0=EQUITY, carry=carry)
    trades, open_pos = res["trades"], res["open_positions"]

    for _, tr in trades.iterrows():
        key = trade_key(tr["sym"], tr["opened"])
        if key in closed_keys:
            continue
        row = tr.to_dict()
        seat = seats.pop(key, TAKEN if key in live_keys else BLOCKED)
        journal_notion.post_shadow_close(row, pages.pop(key, None), seat)
        append_trade(row, seat)
        closed_keys.add(key)
        closed_list.append(key)
        print(f"  shadow CLOSE {key} {tr['reason']} {tr['r']:+.2f}R [{seat}]")

    for sym, pos in open_pos.items():
        key = trade_key(sym, pos.opened_ts)
        if pages.get(key):
            continue
        # `key in pages but None` == a prior run's Notion write failed; retry it
        # every run, exactly as the live journal does.
        seat = seats.setdefault(key, TAKEN if key in live_keys else BLOCKED)
        info = {"sym": sym, "dir": pos.direction, "conf": pos.conf,
                "setup": pos.setup, "regime": pos.regime, "entry": pos.entry,
                "sl": pos.sl0, "tp1": pos.tp1, "leverage": pos.leverage,
                "hold_entry": pos.meta.get("hold_entry", 1.0),
                "opened": pos.opened_ts}
        if key not in pages:
            print(f"  shadow OPEN {key} {pos.setup} conf={pos.conf:.2f} [{seat}]")
        pages[key] = journal_notion.post_shadow_open(info, seat)

    n_same_sym = same_symbol_blocked(args[2], open_pos, latest_bar)
    print(f"  shadow book: {len(trades)} closed, {len(open_pos)} open "
          f"(+{n_same_sym} same-symbol candidates dropped)")
    return {
        "shadow_carry": serialize_carry(
            # never compound: the shadow account is a unit of measure, not money
            {**res["carry"], "cash": EQUITY, "peak": EQUITY}),
        "shadow_pages": pages,
        "shadow_seats": seats,
        "shadow_closed_keys": closed_list[-2000:],
    }
