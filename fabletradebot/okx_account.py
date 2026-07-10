"""Read-only OKX account data via the 3-key auth (balance + positions).

Unlike order placement, reads are safe whenever the keys are present — no
LIVE_CONFIRM gate. Every function returns None (never raises) when keys are
missing or the request fails, so callers can degrade gracefully to the
paper-trading equity.
"""
from .okx_auth import has_keys, signed_request


def fetch_equity(ccy: str = "USDT") -> float | None:
    """Total account equity in `ccy`, or None if unavailable."""
    if not has_keys():
        return None
    try:
        resp = signed_request("GET", f"/api/v5/account/balance?ccy={ccy}")
        if resp.get("code") != "0" or not resp.get("data"):
            return None
        details = resp["data"][0].get("details", [])
        for d in details:
            if d.get("ccy") == ccy:
                return float(d.get("eq", 0.0))
        return float(resp["data"][0].get("totalEq", 0.0))
    except Exception as exc:
        print(f"[okx] balance fetch failed: {exc}")
        return None


def fetch_positions() -> dict | None:
    """Open swap positions as {instId: signed_contracts}, or None."""
    if not has_keys():
        return None
    try:
        resp = signed_request("GET", "/api/v5/account/positions?instType=SWAP")
        if resp.get("code") != "0":
            return None
        out = {}
        for p in resp.get("data", []):
            pos = float(p.get("pos", 0.0))
            side = p.get("posSide", "net")
            if side == "short":
                pos = -abs(pos)
            out[p["instId"]] = pos
        return out
    except Exception as exc:
        print(f"[okx] positions fetch failed: {exc}")
        return None
