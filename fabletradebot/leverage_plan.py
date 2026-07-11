"""Confidence-tiered ACCOUNT leverage plan — the honest "10x/5x/3x/2x".

REDESIGN_V4 §1 falsified five variants of conviction-scaled POSITION SIZING:
the XS edge is linear in the signal, so betting more on "stronger" signals
grows variance faster than return. What conviction CAN legitimately choose
is the exchange-side hard stop sitting ABOVE the software ceiling — the
account leverage tier per instrument. The tier never sizes a position (vol
targeting does); it decides
  (a) the absolute notional cap if the software ever misbehaves, and
  (b) how much margin backs the position when run isolated, i.e. how far
      away forced liquidation sits.

Tier = min(confidence tier, liquidation-safety tier)
  confidence  reuses the tested conviction() machinery (signal strength x
              horizon agreement x dispersion regime), scaled by the same
              drawdown headroom the governor applies to the book
  safety      largest leverage whose approximate isolated liquidation
              distance (1/L - maintenance margin) still covers LIQ_SIGMAS
              daily sigmas of that asset — the whale's survival rule

The ladder itself comes from cfg.conv_tiers (10/5/3/2 by confidence cuts
0.80/0.60/0.35). Only the leverage ceilings are used here; the vol-budget
multipliers in that table are the falsified half and stay dead.
"""
import numpy as np

from .v3 import V3Config, _tier, conviction

LIQ_SIGMAS = 4.0        # liquidation must sit at least this many daily sigmas away
MAINT_MARGIN = 0.01     # conservative maintenance-margin haircut (OKX tier 1)


def _governor_mult(equity_dd: float, cfg: V3Config) -> float:
    """Same linear de-risk factor target_weights applies between the bands."""
    if equity_dd > cfg.dd_soft:
        return 1.0
    span = cfg.dd_hard - cfg.dd_soft
    frac = (equity_dd - cfg.dd_soft) / span if span < 0 else 1.0
    return max(cfg.dd_floor, 1.0 + (cfg.dd_floor - 1.0) * min(frac, 1.0))


def safety_max_leverage(vol_ann: float,
                        liq_sigmas: float = LIQ_SIGMAS,
                        maint_margin: float = MAINT_MARGIN) -> float:
    """Largest leverage whose isolated liquidation distance covers
    liq_sigmas daily sigmas: 1/L - mm >= k * sigma_day."""
    sigma_day = vol_ann / np.sqrt(365.0)
    return 1.0 / (liq_sigmas * sigma_day + maint_margin)


def plan_leverage(weights: dict, sig_row: dict, equity_dd: float,
                  cfg: V3Config) -> dict:
    """Per-position account-leverage plan for the current target book.

    weights: {asset: signed target weight}; sig_row: {asset: signal dict with
    xs/agree/disp_pct/vol_ann}. Returns per-asset tier + margin math and the
    account totals. Purely advisory/plumbing: changes no target weight.
    """
    ladder = sorted({lev for _, lev, _ in cfg.conv_tiers}, reverse=True)
    gov = _governor_mult(equity_dd, cfg)
    out, total_margin = {}, 0.0
    for a, w in weights.items():
        if abs(w) < 1e-9:
            continue
        s = sig_row[a]
        vol_ann = float(s["vol_ann"])
        if not np.isfinite(vol_ann) or vol_ann <= 0:
            continue
        conf = conviction(s, cfg) * gov
        conf_tier = _tier(conf, cfg)[0]
        safe_l = safety_max_leverage(vol_ann)
        tier = next((t for t in ladder if t <= min(conf_tier, safe_l)),
                    ladder[-1])
        sigma_day = vol_ann / np.sqrt(365.0)
        liq_dist = 1.0 / tier - MAINT_MARGIN
        margin = abs(w) / tier
        total_margin += margin
        out[a] = dict(
            weight=round(float(w), 4),
            confidence=round(float(conf), 3),
            tier=float(tier),
            margin_frac=round(float(margin), 4),
            liq_dist=round(float(liq_dist), 4),
            liq_sigmas=round(float(liq_dist / sigma_day), 2) if sigma_day > 0 else None,
            safety_bound=bool(safe_l < conf_tier),
        )
    return dict(assets=out, total_margin_frac=round(float(total_margin), 4),
                governor_mult=round(float(gov), 3))


def format_plan(plan: dict) -> str:
    """One-line-per-asset console/Telegram rendering."""
    lines = []
    for a, p in plan["assets"].items():
        side = "LONG" if p["weight"] > 0 else "SHORT"
        guard = " (safety-capped)" if p["safety_bound"] else ""
        lines.append(
            f"{a} {side} w={p['weight']:+.3f} -> {p['tier']:.0f}x tier"
            f" | conf {p['confidence']:.2f} | margin {p['margin_frac']:.1%}"
            f" | liq {p['liq_dist']:.0%} away (~{p['liq_sigmas']:.1f} sigma_day){guard}")
    lines.append(f"total isolated margin {plan['total_margin_frac']:.1%} of equity"
                 f" | governor x{plan['governor_mult']:.2f}")
    return "\n".join(lines)
