"""Confidence -> (leverage tier, risk fraction); liquidation-safety cap; sizing.

Honesty note (see BLUEPRINT §1): with fixed per-trade risk the notional is
risk/stop_frac; the leverage number chooses margin efficiency and where the
liquidation price sits, and acts as a notional cap. It does NOT multiply PnL.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

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
                   asset_cap: float, p: Params,
                   target: float | None = None) -> tuple[float, float]:
    """(leverage, risk_frac). leverage==0 means the trade is not allowed.

    `target` overrides Params.stop_loss_target for one slot — the asymmetry
    hook. It carries the per-trade account risk, so a slot whose thesis has
    weaker evidence behind it can bet less without changing anything about how
    the trade is entered, managed or exited (R is leverage-independent, so this
    moves only the equity path).
    """
    lev_c, risk = conf_tier(conf, p)
    if lev_c == 0.0 or stop_frac <= 0:
        return 0.0, 0.0
    p = p if target is None else replace(p, stop_loss_target=target)
    if p.stop_loss_target > 0:
        # V8: leverage from the stop, not from conf (see Params.stop_loss_target).
        # conf_tier still runs above -- its zero return is the conf_entry gate --
        # but only its risk fraction survives; the tier's leverage is discarded.
        lev_c = p.stop_loss_target / stop_frac
    caps = min(p.regime_lev_cap.get(regime_state, 0.0),
               lev_liq_cap(stop_frac, p), asset_cap)
    tiered = floor_tier(min(lev_c, caps))
    if tiered == 0.0 and p.stop_loss_target > 0 and caps >= TIERS[0]:
        # The target wants under 2x, but 2x is the smallest tier there is, so
        # the old behaviour refused the trade outright. Leverage is quantised;
        # margin_frac is not, so take the floor tier and let the caller cut
        # margin by `target / (lev * stop_frac)` instead. The per-stop loss
        # still lands on the target -- only the refusal disappears.
        # Callers MUST apply that correction; engine.py does at both entries.
        tiered = TIERS[0]
    return tiered, risk


def slot_target(p: Params, setup: str) -> float:
    """Per-slot account-risk target, or 0 when this profile does not use one.

    A playbook override must never ARM risk-derived leverage in a profile whose
    Params.stop_loss_target is 0 (i.e. one still sizing off conf tiers) -- that
    would silently switch base/turbo/max onto a different sizing rule for the
    handful of slots carrying an override. The profile decides whether the
    feature is on; the slot only decides how much, once it is.
    """
    if p.stop_loss_target <= 0:
        return 0.0
    return float(p.playbooks.get(setup, {}).get("stop_loss_target",
                                                p.stop_loss_target))


@dataclass(frozen=True)
class Sizing:
    notional: float
    margin: float
    risk_amt: float
    leverage: float
    liq_price: float


def size_position(equity: float, risk_frac: float, entry: float, sl: float,
                  direction: int, leverage: float, full_margin: bool = False,
                  margin_frac: float = 1.0) -> Sizing:
    stop_frac = abs(entry - sl) / entry
    # full_margin (whale mode): deploy the account as margin at the
    # confidence-chosen leverage — notional = equity*lev*margin_frac, so at
    # margin_frac==1 the WHOLE account is the margin. margin_frac<1 is how the
    # drawdown governor (dd_half) and correlation halving de-risk in whale mode:
    # they can't touch risk_frac (unused here), so they scale the deployed
    # margin instead — the stop-before-liquidation invariant is untouched
    # because `leverage` (hence the liq distance) is unchanged.
    if full_margin:
        notional = equity * leverage * margin_frac
    else:
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
