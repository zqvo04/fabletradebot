"""Notion trade journal + signal-position log (optional). Fires only when
NOTION_TOKEN and the relevant database id are set; failures are logged and
never break the trade loop.

Trade journal DB (NOTION_DATABASE_ID) — v2 discrete trades:
  Name (title) | Asset | Playbook | Direction | R | PnL | Reason | Closed

Signal-position DB (NOTION_SIGNAL_DB_ID) — scored v3/v4/v5 signals:
  Name (title) | Bar Time (date) | System | Asset | Direction | Status
  Entry/TP/SL/Exit/Result R/PnL %/Leverage/Target Weight/Equity (number)
  | Closed (date)

Leverage = confidence-tiered ACCOUNT leverage (2/3/5/10x hard stop), not a
sizing multiplier. PnL % = signed price-move return of the signal at close.
Extra properties are ignored by Notion if the column is absent, so a DB
created for the older schema keeps working — add the columns to see them.
"""
import json
import os
import urllib.request

_NOTION_VERSION = "2022-06-28"
_BASE = "https://api.notion.com/v1/pages"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('NOTION_TOKEN')}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(url: str, body: dict, method: str) -> dict | None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # journal must never break the trade loop
        print(f"[journal] Notion {method} failed: {exc}")
        return None


def _dir_str(d: int) -> str:
    return "LONG" if d > 0 else "SHORT" if d < 0 else "FLAT"


def _scored_props(pos: dict) -> dict:
    """Notion properties for a scored position (open or resolved)."""
    d = _dir_str(pos["direction"])
    props = {
        "Name": {"title": [{"text": {"content":
                 f"{pos['asset']} {pos['system']} {d} @ {pos['entry']:.6g}"}}]},
        "System": {"select": {"name": pos["system"]}},
        "Asset": {"select": {"name": pos["asset"]}},
        "Direction": {"select": {"name": d}},
        "Status": {"select": {"name": pos["status"]}},
        "Entry": {"number": round(float(pos["entry"]), 8)},
        "TP": {"number": round(float(pos["tp"]), 8)},
        "SL": {"number": round(float(pos["sl"]), 8)},
        "Target Weight": {"number": round(float(pos["weight"]), 6)},
        "Equity": {"number": round(float(pos["equity"]), 2)},
        "Bar Time": {"date": {"start": str(pos["opened_ts"])}},
    }
    if pos.get("leverage") is not None:
        props["Leverage"] = {"number": round(float(pos["leverage"]), 1)}
    if pos.get("exit") is not None:
        props["Exit"] = {"number": round(float(pos["exit"]), 8)}
    if pos.get("result_r") is not None:
        props["Result R"] = {"number": round(float(pos["result_r"]), 4)}
    if pos.get("result_pct") is not None:
        props["PnL %"] = {"number": round(float(pos["result_pct"]), 3)}
    if pos.get("closed_ts"):
        props["Closed"] = {"date": {"start": str(pos["closed_ts"])}}
    return props


def post_scored(pos: dict) -> str | None:
    """Create a signal-position row. Returns the Notion page id, or None."""
    if not os.environ.get("NOTION_TOKEN") or not os.environ.get("NOTION_SIGNAL_DB_ID"):
        return None
    body = {"parent": {"database_id": os.environ["NOTION_SIGNAL_DB_ID"]},
            "properties": _scored_props(pos)}
    resp = _request(_BASE, body, "POST")
    return resp.get("id") if resp else None


def update_scored(page_id: str, pos: dict) -> bool:
    """Patch an existing row to its resolved Status/Exit/Result R/Closed."""
    if not os.environ.get("NOTION_TOKEN") or not page_id:
        return False
    props = {"Status": {"select": {"name": pos["status"]}}}
    if pos.get("exit") is not None:
        props["Exit"] = {"number": round(float(pos["exit"]), 8)}
    if pos.get("result_r") is not None:
        props["Result R"] = {"number": round(float(pos["result_r"]), 4)}
    if pos.get("result_pct") is not None:
        props["PnL %"] = {"number": round(float(pos["result_pct"]), 3)}
    if pos.get("closed_ts"):
        props["Closed"] = {"date": {"start": str(pos["closed_ts"])}}
    return _request(f"{_BASE}/{page_id}", {"properties": props}, "PATCH") is not None


def post_trade(trade: dict) -> bool:
    db = os.environ.get("NOTION_DATABASE_ID")
    if not os.environ.get("NOTION_TOKEN") or not db:
        return False
    direction = _dir_str(trade["direction"])
    title = f"{trade['asset']} {trade['playbook']} {direction} ({trade['r']:+.2f}R)"
    body = {
        "parent": {"database_id": db},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Asset": {"select": {"name": trade["asset"]}},
            "Playbook": {"select": {"name": trade["playbook"]}},
            "Direction": {"select": {"name": direction}},
            "R": {"number": round(float(trade["r"]), 4)},
            "PnL": {"number": round(float(trade["pnl"]), 2)},
            "Reason": {"select": {"name": trade["reason"]}},
            "Closed": {"date": {"start": str(trade["closed_ts"])}},
        },
    }
    return _request(_BASE, body, "POST") is not None
