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


def _enabled() -> bool:
    return bool(os.environ.get("NOTION_TOKEN") and os.environ.get("NOTION_SIGNAL_DB_ID"))


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
                 f"OPEN mtm @ {mtm['price']:.6g} | {mtm['r']:+.2f}R | "
                 f"setup {mtm['setup']} | regime {mtm['regime']} | "
                 f"hold_conf {mtm.get('hold_conf', 0.0):.2f}"}}]},
    }
    return _request(f"{_BASE}/{page_id}", {"properties": props}, "PATCH") is not None


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
                 f"setup {tr['setup']} | regime {tr['regime']} | exit {tr['reason']}"}}]},
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
