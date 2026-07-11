"""Confidence -> (leverage tier, risk fraction); liquidation-safety cap; sizing.

Honesty note (see BLUEPRINT §1): with fixed per-trade risk the notional is
risk/stop_frac; the leverage number chooses margin efficiency and where the
liquidation price sits, and acts as a notional cap. It does NOT multiply PnL.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Params

TIERS = (2.0, 3.0, 5.0, 10.0)
LIQ_MMR = 0.01  # maintenance-margin fraction used for the liq-price estimate;
                # config.mmr_buffer (0.015) used in the CAP is stricter on purpose.


def conf_tier(conf: float, p: Params) -> tuple[float, float]:
    """(leverage tier, risk fraction) for a confidence level; (0,0) if below entry."""
    lev, risk = 0.0, 0.0
    for lo, tier_lev, tier_risk in p.conf_tiers:
        if conf >= lo:
            lev, risk = tier_lev, tier_risk
    return lev, risk


def lev_liq_cap(stop_frac: float, p: Params) -> float:
    """Max leverage such that liquidation distance >= liq_stop_mult * stop distance."""
    return 1.0 / (p.liq_stop_mult * stop_frac + p.mmr_buffer)


def floor_tier(lev: float) -> float:
    """Largest standard tier <= lev; 0 when even 2x is unsafe."""
    out = 0.0
    for t in TIERS:
        if lev >= t:
            out = t
    return out


def final_leverage(conf: float, stop_frac: float, regime_state: str,
                   asset_cap: float, p: Params) -> tuple[float, float]:
    """(leverage, risk_frac). leverage==0 means the trade is not allowed."""
    lev_c, risk = conf_tier(conf, p)
    if lev_c == 0.0 or stop_frac <= 0:
        return 0.0, 0.0
    lev = min(lev_c, p.regime_lev_cap.get(regime_state, 0.0),
              lev_liq_cap(stop_frac, p), asset_cap)
    return floor_tier(lev), risk


@dataclass(frozen=True)
class Sizing:
    notional: float
    margin: float
    risk_amt: float
    leverage: float
    liq_price: float


def size_position(equity: float, risk_frac: float, entry: float, sl: float,
                  direction: int, leverage: float) -> Sizing:
    stop_frac = abs(entry - sl) / entry
    notional = min(equity * risk_frac / stop_frac, equity * leverage)
    risk_amt = notional * stop_frac   # == equity*risk_frac unless the cap binds
    margin = notional / leverage
    liq_frac = 1.0 / leverage - LIQ_MMR
    liq_price = entry * (1 - direction * liq_frac)
    # structural invariant: the stop must always be hit before liquidation
    if direction * (sl - liq_price) <= 0:
        raise AssertionError(
            f"liquidation safety violated: sl={sl} liq={liq_price} dir={direction}")
    return Sizing(notional, margin, risk_amt, leverage, liq_price)
