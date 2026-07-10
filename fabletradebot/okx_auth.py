"""Shared OKX v5 request signing (3-key auth).

Single source of the HMAC-SHA256 signing used by both the read-only account
fetcher (okx_account) and the live order adapter (okx_exec). Auth needs all
three of OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE; set OKX_DEMO=1 to hit
OKX's demo-trading environment (adds the x-simulated-trading header).
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.request
from datetime import datetime, timezone

from .data_okx import BASE

KEYS = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE")


def has_keys() -> bool:
    return all(os.environ.get(k) for k in KEYS)


def signed_request(method: str, path: str, body: dict | None = None,
                   timeout: int = 30) -> dict:
    """Signed OKX request. `path` must include any query string (it is part
    of the signature prehash). Raises if the 3 keys are not set."""
    if not has_keys():
        raise RuntimeError("OKX auth requires OKX_API_KEY/OKX_API_SECRET/OKX_PASSPHRASE")
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())
