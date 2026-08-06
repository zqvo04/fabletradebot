"""Notion journal — reuses the v5 `FableTradeBot — Signal Log` database.

Enabled only when NOTION_TOKEN + NOTION_SIGNAL_DB_ID are set. Every property
and select option written here EXISTS in that DB (System option "V1",
Asset options for the full universe, plus Confidence / Lev PnL % / Hold Hours
number columns were added to the DB schema before this code shipped) —
Notion 400-rejects a whole request if any property/option is unknown.
Failures are printed and never break the trade loop.
"""
from __future__ import annotations

import json
import os
import urllib.request

_VERSION = "2022-06-28"
_BASE = "https://api.notion.com/v1/pages"
SYSTEM = "V1"


def _enabled(db_env: str = "NOTION_SIGNAL_DB_ID") -> bool:
    token = os.environ.get("NOTION_TOKEN")
    db = os.environ.get(db_env)
    if token and db:
        return True
    # loud, not silent: a workflow log showing NOTION_TOKEN: *** only proves the
    # env var was DECLARED, not that the underlying secret has a real value —
    # an empty/missing repo secret renders the same masked "***" in the log,
    # so without this line a misconfigured secret looks identical to a working
    # one (entries print "OPEN" regardless; Notion gets nothing, silently)
    missing = [n for n, v in (("NOTION_TOKEN", token), (db_env, db)) if not v]
    print(f"[journal] Notion disabled — missing/empty env var(s): {', '.join(missing)}")
    return False


def _request(url: str, body: dict, method: str) -> dict | None:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method=method,
        headers={"Authorization": f"Bearer {os.environ.get('NOTION_TOKEN')}",
                 "Notion-Version": _VERSION, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(f"[journal] Notion {method} {exc.code}: {exc.read()[:300]}")
    except Exception as exc:
        print(f"[journal] Notion {method} failed: {exc}")
    return None


def _status(reason: str, pnl: float) -> str:
    if reason == "Timeout":
        return "Timeout-Win" if pnl > 0 else "Timeout-Loss"
    return "Win" if pnl > 0 else "Loss"


def post_open(pos: dict) -> str | None:
    """Create an Open row; returns page id for the later close update."""
    if not _enabled():
        return None
    d = "LONG" if pos["dir"] > 0 else "SHORT"
    props = {
        "Name": {"title": [{"text": {"content":
                 f"{pos['sym']} {SYSTEM} {d} {pos['leverage']:.0f}x @ {pos['entry']:.6g}"}}]},
        "System": {"select": {"name": SYSTEM}},
        "Asset": {"select": {"name": pos["sym"]}},
        "Direction": {"select": {"name": d}},
        "Status": {"select": {"name": "Open"}},
        "Entry": {"number": round(float(pos["entry"]), 8)},
        "SL": {"number": round(float(pos["sl"]), 8)},
        "TP": {"number": round(float(pos["tp1"]), 8)},
        "Leverage": {"number": round(float(pos["leverage"]), 1)},
        "Confidence": {"number": round(float(pos["conf"]), 3)},
        "Equity": {"number": round(float(pos["equity"]), 2)},
        "Bar Time": {"date": {"start": str(pos["opened"])}},
        "Note": {"rich_text": [{"text": {"content":
                 f"setup {pos['setup']} | regime {pos['regime']}"}}]},
    }
    resp = _request(_BASE, {"parent": {"database_id": os.environ["NOTION_SIGNAL_DB_ID"]},
                            "properties": props}, "POST")
    return resp.get("id") if resp else None


def update_open(page_id: str | None, mtm: dict) -> bool:
    """Hourly mark-to-market of a still-open row (scoring runs alongside the
    trade loop, brief §10). Status stays 'Open'; unrealized R / PnL% / hold
    hours and the current price/stop are refreshed each run. No-op without a
    page id or secrets. Failures never break the loop."""
    if not _enabled() or not page_id:
        return False
    props = {
        "Status": {"select": {"name": "Open"}},
        "Result R": {"number": round(float(mtm["r"]), 4)},
        "PnL %": {"number": round(float(mtm["pnl_pct_price"]), 3)},
        "Lev PnL %": {"number": round(float(mtm["pnl_pct_lev"]), 3)},
        "Hold Hours": {"number": int(mtm["bars"])},
        "SL": {"number": round(float(mtm["sl"]), 8)},
        "Note": {"rich_text": [{"text": {"content":
                 f"OPEN mtm @ {mtm['price']:.6g} | {mtm['r']:+.2f}R "
                 f"(peak {mtm.get('peak_r', 0.0):+.2f}R) | "
                 f"setup {mtm['setup']} | regime {mtm['regime']} | "
                 f"hold_conf {mtm.get('hold_conf', 0.0):.2f}"}}]},
    }
    return _request(f"{_BASE}/{page_id}", {"properties": props}, "PATCH") is not None


SHADOW_DB = "NOTION_SHADOW_DB_ID"
SHADOW_SYSTEM = "V1-shadow"


def _shadow_ident(row: dict, seat_state: str) -> dict:
    """Properties shared by a shadow row's create and its resolution."""
    d = "LONG" if row["dir"] > 0 else "SHORT"
    return {
        "Name": {"title": [{"text": {"content":
                 f"{row['sym']} shadow {d} {row['leverage']:.0f}x "
                 f"@ {row['entry']:.6g}"}}]},
        "System": {"select": {"name": SHADOW_SYSTEM}},
        "Asset": {"select": {"name": row["sym"]}},
        "Direction": {"select": {"name": d}},
        "Seat State": {"select": {"name": seat_state}},
        "Setup": {"select": {"name": row["setup"]}},
        "Regime": {"select": {"name": row["regime"]}},
        "Entry": {"number": round(float(row["entry"]), 8)},
        "Leverage": {"number": round(float(row["leverage"]), 1)},
        "Confidence": {"number": round(float(row["conf"]), 3)},
        "Hold Entry": {"number": round(float(row.get("hold_entry", 1.0)), 4)},
        "Bar Time": {"date": {"start": str(row["opened"])}},
    }


def post_shadow_open(pos: dict, seat_state: str) -> str | None:
    """Create an Open row in the shadow DB; returns the page id for the close.

    No hourly mark-to-market follows (unlike the live journal): nobody watches
    this DB in real time and the learning signal is the RESOLVED trade, so the
    row is written exactly twice — once here, once at its exit.
    """
    if not _enabled(SHADOW_DB):
        return None
    props = {
        **_shadow_ident(pos, seat_state),
        "Status": {"select": {"name": "Open"}},
        "SL": {"number": round(float(pos["sl"]), 8)},
        "TP": {"number": round(float(pos["tp1"]), 8)},
    }
    resp = _request(_BASE, {"parent": {"database_id": os.environ[SHADOW_DB]},
                            "properties": props}, "POST")
    return resp.get("id") if resp else None


def post_shadow_close(tr: dict, page_id: str | None, seat_state: str) -> str | None:
    """Resolve a shadow row (or create it resolved if the open write failed)."""
    if not _enabled(SHADOW_DB):
        return None
    peak = float(tr.get("peak_r", 0.0))
    giveback = max(0.0, peak - float(tr["r"]))
    props = {
        "Status": {"select": {"name": _status(tr["reason"], tr["pnl"])}},
        "Exit Reason": {"select": {"name": tr["reason"]}},
        "Exit": {"number": round(float(tr["exit"]), 8)},
        "Result R": {"number": round(float(tr["r"]), 4)},
        "Peak R": {"number": round(peak, 4)},
        "Giveback R": {"number": round(giveback, 4)},
        "PnL %": {"number": round(float(tr["pnl_pct_price"]), 3)},
        "Lev PnL %": {"number": round(float(tr["pnl_pct_lev"]), 3)},
        "Hold Hours": {"number": int(tr["bars"])},
        "Closed": {"date": {"start": str(tr["closed"])}},
        "Note": {"rich_text": [{"text": {"content":
                 f"shadow ({seat_state}) | setup {tr['setup']} | "
                 f"regime {tr['regime']} | exit {tr['reason']} | "
                 f"peak {peak:+.2f}R (gave back {giveback:+.2f}R)"}}]},
    }
    if page_id:
        resp = _request(f"{_BASE}/{page_id}", {"properties": props}, "PATCH")
        return page_id if resp else None
    props.update(_shadow_ident(tr, seat_state))
    resp = _request(_BASE, {"parent": {"database_id": os.environ[SHADOW_DB]},
                            "properties": props}, "POST")
    return resp.get("id") if resp else None


def post_close(tr: dict, page_id: str | None) -> str | None:
    """Update the Open row to its resolution (or create a resolved row if the
    open row was never journaled)."""
    if not _enabled():
        return None
    props = {
        "Status": {"select": {"name": _status(tr["reason"], tr["pnl"])}},
        "Exit": {"number": round(float(tr["exit"]), 8)},
        "Result R": {"number": round(float(tr["r"]), 4)},
        "PnL %": {"number": round(float(tr["pnl_pct_price"]), 3)},
        "Lev PnL %": {"number": round(float(tr["pnl_pct_lev"]), 3)},
        "Hold Hours": {"number": int(tr["bars"])},
        "Equity": {"number": round(float(tr["equity_after"]), 2)},
        "Closed": {"date": {"start": str(tr["closed"])}},
        "Note": {"rich_text": [{"text": {"content":
                 f"setup {tr['setup']} | regime {tr['regime']} | exit {tr['reason']} | "
                 f"peak {tr.get('peak_r', 0.0):+.2f}R "
                 f"(gave back {max(0.0, tr.get('peak_r', 0.0) - tr['r']):+.2f}R)"}}]},
    }
    if page_id:
        resp = _request(f"{_BASE}/{page_id}", {"properties": props}, "PATCH")
        return page_id if resp else None
    d = "LONG" if tr["dir"] > 0 else "SHORT"
    props.update({
        "Name": {"title": [{"text": {"content":
                 f"{tr['sym']} {SYSTEM} {d} {tr['leverage']:.0f}x @ {tr['entry']:.6g}"}}]},
        "System": {"select": {"name": SYSTEM}},
        "Asset": {"select": {"name": tr["sym"]}},
        "Direction": {"select": {"name": d}},
        "Entry": {"number": round(float(tr["entry"]), 8)},
        "Leverage": {"number": round(float(tr["leverage"]), 1)},
        "Confidence": {"number": round(float(tr["conf"]), 3)},
        "Bar Time": {"date": {"start": str(tr["opened"])}},
    })
    resp = _request(_BASE, {"parent": {"database_id": os.environ["NOTION_SIGNAL_DB_ID"]},
                            "properties": props}, "POST")
    return resp.get("id") if resp else None
