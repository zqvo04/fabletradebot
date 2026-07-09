"""Notion trade journal (optional). Fires only when NOTION_TOKEN and
NOTION_DATABASE_ID are set; failures are logged and never break the trade loop.

Expected Notion database properties:
  Name (title) | Asset (select) | Playbook (select) | Direction (select)
  R (number) | PnL (number) | Reason (select) | Closed (date)
"""
import json
import os
import urllib.request


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
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
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
