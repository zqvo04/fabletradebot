"""OKX v5 order-execution adapter for LIVE mode.

SAFETY: live orders require ALL of
  TRADE_MODE=live, OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE, LIVE_CONFIRM=YES
Anything less -> orders are printed, not sent. This adapter is intentionally
minimal (market orders in net mode) and is NOT exercised by tests — do not
enable live mode before verifying against OKX's demo-trading endpoint
(header `x-simulated-trading: 1`, set OKX_DEMO=1).
"""
import os

from .data_okx import INSTRUMENTS
from .okx_auth import has_keys, signed_request


def _live_enabled() -> bool:
    return (os.environ.get("TRADE_MODE") == "live"
            and os.environ.get("LIVE_CONFIRM") == "YES"
            and has_keys())


def place_market_order(asset: str, side: str, qty: float) -> dict | None:
    """side: 'buy' | 'sell'; qty in contracts. Dry-runs unless fully armed."""
    order = {
        "instId": INSTRUMENTS[asset], "tdMode": "cross",
        "side": side, "ordType": "market", "sz": str(qty),
    }
    if not _live_enabled():
        print(f"[exec] DRY RUN (live not armed): {order}")
        return None
    resp = signed_request("POST", "/api/v5/trade/order", order)
    if resp.get("code") != "0":
        raise RuntimeError(f"order rejected: {resp}")
    return resp
