"""Notion trade journal + signal log (optional). Fires only when NOTION_TOKEN
and the relevant database id are set; failures are logged and never break the
trade loop.

Trade journal DB (NOTION_DATABASE_ID) properties:
  Name (title) | Asset (select) | Playbook (select) | Direction (select)
  R (number) | PnL (number) | Reason (select) | Closed (date)

Signal-log DB (NOTION_SIGNAL_DB_ID) properties (created by the setup agent):
  Name (title) | Bar Time (date) | System (select) | Asset (select)
  Direction (select) | Target Weight/Prev Weight/Delta/Equity (number) | Note (text)
"""
import json
import os
import urllib.request

_NOTION_VERSION = "2022-06-28"


def _post_page(body: dict) -> bool:
    token = os.environ.get("NOTION_TOKEN")
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status < 300
    except Exception as exc:  # journal must never break the trade loop
        print(f"[journal] Notion post failed: {exc}")
        return False


def post_signal(sig: dict) -> bool:
    """Log one v2/v3 signal firing to the signal-log database."""
    token = os.environ.get("NOTION_TOKEN")
    db = os.environ.get("NOTION_SIGNAL_DB_ID")
    if not token or not db:
        return False
    title = (f"{sig['asset']} {sig['system']} {sig['direction']} "
             f"({sig['target_weight']:+.3f})")
    props = {
        "Name": {"title": [{"text": {"content": title}}]},
        "System": {"select": {"name": sig["system"]}},
        "Asset": {"select": {"name": sig["asset"]}},
        "Direction": {"select": {"name": sig["direction"]}},
        "Target Weight": {"number": round(float(sig["target_weight"]), 6)},
        "Prev Weight": {"number": round(float(sig["prev_weight"]), 6)},
        "Delta": {"number": round(float(sig["delta"]), 6)},
        "Equity": {"number": round(float(sig["equity"]), 2)},
        "Bar Time": {"date": {"start": str(sig["bar_time"])}},
    }
    if sig.get("note"):
        props["Note"] = {"rich_text": [{"text": {"content": str(sig["note"])}}]}
    return _post_page({"parent": {"database_id": db}, "properties": props})


def post_trade(trade: dict) -> bool:
    token = os.environ.get("NOTION_TOKEN")
    db = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db:
        return False
    direction = "LONG" if trade["direction"] > 0 else "SHORT"
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
    return _post_page(body)
