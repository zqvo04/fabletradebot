"""OKX v5 order-execution adapter for LIVE mode.

SAFETY: live orders require ALL of
  TRADE_MODE=live, OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE, LIVE_CONFIRM=YES
Anything less -> orders are printed, not sent. This adapter is intentionally
minimal (market orders in net mode) and is NOT exercised by tests — do not
enable live mode before verifying against OKX's demo-trading endpoint
(header `x-simulated-trading: 1`, set OKX_DEMO=1).
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.request
from datetime import datetime, timezone

from .data_okx import BASE, INSTRUMENTS


def _live_enabled() -> bool:
    return (os.environ.get("TRADE_MODE") == "live"
            and os.environ.get("LIVE_CONFIRM") == "YES"
            and all(os.environ.get(k) for k in
                    ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE")))


def _signed_request(method: str, path: str, body: dict | None = None) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    payload = json.dumps(body) if body else ""
    msg = f"{ts}{method}{path}{payload}"
    sign = base64.b64encode(
        hmac.new(os.environ["OKX_API_SECRET"].encode(), msg.encode(),
                 hashlib.sha256).digest()).decode()
    headers = {
        "OK-ACCESS-KEY": os.environ["OKX_API_KEY"],
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": os.environ["OKX_PASSPHRASE"],
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (fabletradebot)",
    }
    if os.environ.get("OKX_DEMO") == "1":
        headers["x-simulated-trading"] = "1"
    req = urllib.request.Request(f"{BASE}{path}", headers=headers, method=method,
                                 data=payload.encode() if payload else None)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def place_market_order(asset: str, side: str, qty: float) -> dict | None:
    """side: 'buy' | 'sell'; qty in contracts. Dry-runs unless fully armed."""
    order = {
        "instId": INSTRUMENTS[asset], "tdMode": "cross",
        "side": side, "ordType": "market", "sz": str(qty),
    }
    if not _live_enabled():
        print(f"[exec] DRY RUN (live not armed): {order}")
        return None
    resp = _signed_request("POST", "/api/v5/trade/order", order)
    if resp.get("code") != "0":
        raise RuntimeError(f"order rejected: {resp}")
    return resp
