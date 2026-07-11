"""OKX order-execution SKELETON — V1 ships paper-only.

Four locks must ALL be open before any order call is even attempted:
  1. TRADE_MODE=live
  2. OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE set
  3. LIVE_CONFIRM == "I-UNDERSTAND-LIQUIDATION-RISK"
  4. first verified on the OKX demo endpoint (OKX_DEMO=1)

Even with all locks open, V1's place_order refuses to transmit (raises
NotImplementedError) — wiring real orders is a post-G8 (4-week paper gate)
change, so a stray env var can never trade this version.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.okx.com"
CONFIRM_PHRASE = "I-UNDERSTAND-LIQUIDATION-RISK"


def locks_open() -> tuple[bool, str]:
    if os.environ.get("TRADE_MODE") != "live":
        return False, "TRADE_MODE is not 'live'"
    if not all(os.environ.get(k) for k in
               ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE")):
        return False, "OKX keys missing"
    if os.environ.get("LIVE_CONFIRM") != CONFIRM_PHRASE:
        return False, "LIVE_CONFIRM phrase missing"
    return True, "ok"


def _sign(ts: str, method: str, path: str, body: str) -> str:
    mac = hmac.new(os.environ["OKX_API_SECRET"].encode(),
                   f"{ts}{method}{path}{body}".encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _private(method: str, path: str, body_dict: dict | None = None) -> dict:
    body = json.dumps(body_dict) if body_dict else ""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    headers = {
        "OK-ACCESS-KEY": os.environ["OKX_API_KEY"],
        "OK-ACCESS-SIGN": _sign(ts, method, path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": os.environ["OKX_PASSPHRASE"],
        "Content-Type": "application/json",
    }
    if os.environ.get("OKX_DEMO") == "1":
        headers["x-simulated-trading"] = "1"
    req = urllib.request.Request(BASE + path, data=body.encode() if body else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def account_equity() -> float | None:
    """Read-only: USDT equity of the trading account (works in demo too)."""
    ok, why = locks_open()
    if not ok and os.environ.get("TRADE_MODE") != "live":
        # allow read-only equity with keys alone (paper mode uses it for display)
        if not all(os.environ.get(k) for k in
                   ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE")):
            return None
    try:
        data = _private("GET", "/api/v5/account/balance?ccy=USDT")
        return float(data["data"][0]["details"][0]["eq"])
    except Exception as exc:
        print(f"[okx] equity read failed: {exc}")
        return None


def place_order(sym: str, direction: int, notional: float, leverage: float,
                sl: float, tp: float) -> None:
    ok, why = locks_open()
    if not ok:
        raise PermissionError(f"live locks closed: {why}")
    raise NotImplementedError(
        "V1 is paper-only by design. Real order wiring is a post-G8 change "
        "(4-week paper forward gate) and must be verified with OKX_DEMO=1 first.")
