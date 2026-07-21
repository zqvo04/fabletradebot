"""Single source of truth for universe, costs and strategy parameters.

Every tunable lives here so validation can sweep ±20% without touching logic.
All fractions are decimal (0.01 == 1%).
"""
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class AssetSpec:
    symbol: str
    lev_cap: float        # hard per-asset leverage cap
    slippage: float       # one-way slippage fraction
    listed: str           # earliest date to request data from

# Universe. New/illiquid assets default to lev_cap=2, slippage=0.0008.
UNIVERSE: dict[str, AssetSpec] = {s.symbol: s for s in [
    AssetSpec("BTC",  10.0, 0.0002, "2023-01-01"),
    AssetSpec("ETH",  10.0, 0.0002, "2023-01-01"),
    AssetSpec("SOL",   5.0, 0.0004, "2023-01-01"),
    AssetSpec("BNB",   5.0, 0.0004, "2023-01-01"),
    AssetSpec("LINK",  5.0, 0.0004, "2023-01-01"),
    AssetSpec("AVAX",  5.0, 0.0004, "2023-01-01"),
    AssetSpec("DOGE",  5.0, 0.0004, "2023-01-01"),
    AssetSpec("HYPE",  3.0, 0.0006, "2024-12-01"),
    AssetSpec("SUI",   3.0, 0.0006, "2023-05-01"),
    AssetSpec("WLD",   3.0, 0.0006, "2023-07-25"),
    AssetSpec("TAO",   3.0, 0.0006, "2023-11-01"),
    AssetSpec("ONDO",  3.0, 0.0006, "2024-01-20"),
    AssetSpec("LIT",   2.0, 0.0008, "2025-06-01"),
]}

DEFAULT_ASSET = AssetSpec("_NEW_", 2.0, 0.0008, "2024-01-01")

def spec(symbol: str) -> AssetSpec:
    return UNIVERSE.get(symbol, replace(DEFAULT_ASSET, symbol=symbol))

def inst_id(symbol: str) -> str:
    return f"{symbol}-USDT-SWAP"


@dataclass(frozen=True)
class Params:
    # --- confidence -> entry / leverage tier / risk fraction ---
    # Design measurement (EXPERIMENTS E9): confidence does NOT rank R for the
    # surviving BRK signal (corr 0.005), so V1 sizes uniformly at 1% and keeps
    # a single tier. The tier plumbing stays; forward scoring keeps measuring
    # conf predictiveness and tiers can be re-introduced if it materialises.
    # Risk 0.55%/trade: the largest base size that keeps the WHOLE-UNIVERSE
    # trade stream inside the Monte-Carlo survival gate (95%p MDD <= 30%,
    # P(MDD>50%) ~ 0) once the BTC aggression layer is stacked on top (E10).
    conf_entry: float = 0.55
    conf_tiers: tuple = ((0.55, 5.0, 0.005),)
    # --- aggression layer (E10): mechanical, evidence-conditioned — NOT a
    # fitted per-trade quality predictor (those were rejected: E9b, E10a).
    # Enabled per-asset: whole-universe aggression FAILED the survival MC
    # (95%p MDD -53%) while BTC-only passed — staged rollout, BTC first.
    aggression_syms: tuple = ("BTC",)
    pyramid_max: int = 2             # max add-on units per position (0 = off)
    pyramid_trigger_r: float = 2.0   # add every +2R (in initial-stop units)
    eq_boost_mult: float = 1.5       # risk multiplier near equity highs
    eq_boost_dd: float = 0.02        # "near high" = drawdown below this
    # --- liquidation safety (hard, not swept) ---
    liq_stop_mult: float = 3.0       # liq distance must be >= 3x stop distance
    mmr_buffer: float = 0.015        # maintenance-margin + fee buffer
    # --- regime ---
    regime_lev_cap: dict = field(default_factory=lambda: {
        "TREND_UP": 10.0, "TREND_DOWN": 10.0, "RANGE": 5.0,
        "HIGH_VOL": 3.0, "CRISIS": 0.0})
    vol_pct_highvol: float = 80.0
    vol_pct_crisis: float = 90.0
    crash_5d: float = -0.12
    crash_1d: float = -0.07
    hysteresis_bars: int = 2         # 1D bars to confirm a regime switch
    # X-R trend staleness (DOWN-only, E6 asymmetry): demote TREND_DOWN to RANGE
    # when the daily close has not printed a new 20D low for this many 1D bars
    # (a compressing box keeps |EMA20-EMA50|/ATR above 0.5 because gap and ATR
    # shrink together — the ratio cannot end a dead trend; new extremes can).
    # Prevents stale-downtrend continuation ENTRIES (PBK_S/RCL_S) from taking
    # the whale seat; never touches an open position; BRK still fires in RANGE.
    # NOT G5-passed (design-window cost -9% at K=20, H1-loaded, judged path
    # noise; symmetric variant measured -82~-88% and is rejected outright) —
    # arming is an owner override judged by the forward track, SR-D style.
    # 0 = off (default; base and current whale byte-identical).
    trend_stale_days: int = 0
    corr_window_h: int = 720         # 30d of 1H returns
    corr_alert: float = 0.80
    # --- stops (all setups) ---
    # 1H noise sweeps stops placed inside ~2 ATR (EXPERIMENTS E5)
    sl_floor_atr: float = 2.0
    sl_swing_atr: float = 0.6        # buffer beyond the structural level
    # --- playbook matrix (E11): every entry structure is a slot with its own
    # enable flag, direction, gates and exit overrides. A slot may only be
    # enabled when its edge survived both design half-periods after costs —
    # the disabled slots below carry their measured verdicts. Exit fields set
    # to None fall back to the global defaults further down.
    # V2 principled matrix: every slot is an a-priori, textbook-parameter,
    # EVENT-confirmed trigger (a crossing, never a level) detected on the 4H
    # base timeframe and executed by a 1H precision bar. Unproven slots run in
    # paper at risk_scale 0.25 (the largest scale that keeps the universe MC
    # survival gate green, E12) and must EARN size from forward scoring.
    # Proven slots (risk_scale 1.0) displace experimental holders (Upgrade).
    playbooks: dict = field(default_factory=lambda: {
        # swing trend-following, long: THE survivor (E6/E9)
        "BRK_L":   {"enabled": True,  "dir": 1, "risk_scale": 1.0},
        # PBK: CONTINUOUS chart-state trend-pullback accumulation (V3) — reads
        # the current chart every bar instead of waiting for a crossing event.
        # Swing style (trail). Long + short, paper-scaled until forward-proven.
        "PBK_L":   {"enabled": True,  "dir": 1, "risk_scale": 0.20},
        "PBK_S":   {"enabled": True,  "dir": -1, "risk_scale": 0.20},
        # RCL: trend-pullback reclaim — closed 4H bar crosses BACK ABOVE the
        # 4H EMA20 while the 1D trend agrees (mirror short). 1H bar triggers.
        "RCL_L":   {"enabled": True,  "dir": 1, "risk_scale": 0.20},
        "RCL_S":   {"enabled": True,  "dir": -1, "risk_scale": 0.20},
        # OSC: oscillator re-cross (the user-anchor trigger) — RSI(14,4H)
        # crosses back up through 30 -> long / back down through 70 -> short.
        # Mean-reversion style: fixed target + time stop. RANGE + HIGH_VOL.
        "OSC_L":   {"enabled": True,  "dir": 1, "risk_scale": 0.20,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        "OSC_S":   {"enabled": True,  "dir": -1, "risk_scale": 0.20,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        # BND: Bollinger band re-entry — close crosses back INSIDE the 2-sigma
        # band after closing outside it; fade toward value. RANGE only.
        "BND_L":   {"enabled": True,  "dir": 1, "risk_scale": 0.20,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        "BND_S":   {"enabled": True,  "dir": -1, "risk_scale": 0.20,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        # swing trend-following, short: backtest-rejected (12/12 against, E6)
        # but part of the complete matrix — paper-only at reduced risk, must
        # earn size from the forward track (V2 decision, E12)
        "BRK_S":   {"enabled": True, "dir": -1, "risk_scale": 0.20},
        # day-trade pullback fade at the 4H EMA20, long (TREND_UP):
        # REJECTED — sign flip across halves (+0.20% -> -0.24%/24h, E11)
        "FADE_L":  {"enabled": False, "dir": 1,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 24,
                    "trail_atr": 0.0, "biasflip_exit": False},
        # day-trade rally fade, short (TREND_DOWN): REJECTED — below costs
        # and horizon-inconsistent (+0.08%/24h vs -0.39%/48h, E11)
        "FADE_S":  {"enabled": False, "dir": -1,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 24,
                    "trail_atr": 0.0, "biasflip_exit": False},
        # day-trade range-edge fade, long: REJECTED — sign flip
        # (-0.97% -> +1.11%/24h across halves, E11)
        "RANGE_L": {"enabled": False, "dir": 1,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        # day-trade range-edge fade, short: REJECTED — negative both halves
        "RANGE_S": {"enabled": False, "dir": -1,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
    })
    # --- FADE / RANGE playbook shape parameters (a-priori, not fitted) ---
    fade_zone_atr: float = 0.6       # distance band around the 4H EMA20
    fade_wick: float = 0.35          # rejection-wick minimum
    range_lookback: int = 480        # 20d of 1H bars
    range_edge: float = 0.15         # bottom/top fraction of the range
    # --- V2 event-trigger parameters (textbook standards, not fitted) ---
    osc_lo: float = 30.0             # RSI re-cross levels (user anchor: 30/70)
    osc_hi: float = 70.0
    bb_k: float = 2.0                # band re-entry sigma
    # --- V3 continuous PBK (whale-accumulation) shape parameters ---
    pbk_shallow_atr: float = 0.5     # max distance ABOVE 4H EMA20 (in ATR4H)
    pbk_deep_atr: float = 2.0        # max pullback depth below EMA20 before "broken"
    pbk_rsi_lo: float = 40.0         # 1H momentum reset band (long convention)
    pbk_rsi_hi: float = 60.0
    # (CAPREV capitulation-reversal was fully removed after E8 rejection —
    # its positive drift rides on catastrophic MAE paths; see EXPERIMENTS.md)
    # --- BRK: trend-continuation breakout (the surviving family) ---
    brk_lookback: int = 168          # 7d of 1H bars
    brk_vol_mult: float = 1.2
    bbw_lookback: int = 240          # squeeze percentile window (BRK quality score)
    # --- S4 funding modifier ---
    funding_z_ext: float = 1.5
    funding_bonus: float = 0.05
    funding_penalty: float = 0.10
    funding_z_window: int = 270      # 90d of 8h fundings
    # --- exits ---
    # Exit structure chosen by measurement (E9): partial-TP and time stops CUT
    # the trend winners this signal lives on. 0 disables either mechanism.
    tp1_r: float = 0.0               # 0 = no partial take-profit
    tp1_frac: float = 0.5
    trail_atr: float = 8.0           # wide chandelier, active from entry
    time_stop_bars: int = 0          # 0 = no time stop
    time_stop_min_r: float = 0.3
    # --- X-A stall-tightened chandelier (V6, EXIT_REDESIGN.md §2) -------------
    # A WINNING trend position that stops printing new best_closes for
    # stall_bars consecutive bars (gone flat — neither running nor breaking
    # down) has its chandelier width ratcheted from trail_atr down to
    # stall_trail_atr, banking the stalled winner near its high instead of
    # holding a dead seat through a sideways drift back out to the wide stop.
    # ONE-WAY: once tightened the width never widens back, even if a fresh
    # best_close later prints. Gated on peak_r>=stall_peak_r so only a proven
    # winner is touched (a loser is the SL/LossFade's job, never this axis).
    # A genuinely running trend keeps printing new best_closes, so its stall
    # counter never reaches the threshold — the runner is STRUCTURALLY immune,
    # which is what keeps this off the E9/E16 failure mode (those cut runners
    # unconditionally; this fires only on the flat-and-stuck case). Parameters
    # are a-priori: 24 bars = 1 day flat, 3 ATR = textbook chandelier, 0.5R =
    # "was actually a winner". 0 = off (default); pre-registered candidate that
    # must earn adoption via the G5 gate (EXIT_REDESIGN.md §3) before arming.
    stall_bars: int = 0
    stall_trail_atr: float = 3.0
    stall_peak_r: float = 0.5
    # --- position health / momentum-fade management (V4) ---
    # The hourly scoring loop re-scores every OPEN position (hold_confidence:
    # MTF alignment + regime fit + 4H momentum). When that live conviction
    # decays below hold_conf_exit for hold_conf_bars consecutive bars, the
    # thesis is judged spent and the position is closed (SignalFade) so the
    # seat is freed for the next signal (wait, or rotate into a better coin).
    # Only applies to trend slots (those using the bias-based exit); mean-
    # reversion slots keep their own target/time exits. 0 disables (default).
    # It is a PROFIT-PROTECTING exit: it only ever banks a position already in
    # profit (a losing one is left to the stop) once it has run to at least
    # hold_conf_min_r, then closes it on EITHER read that the move is done:
    #   - momentum lost: gave back hold_giveback of the peak unrealized R (a
    #     stalled run rolling over — the "모멘텀을 잃음" case),
    #   - new situation: live conviction (hold_confidence) collapsed below
    #     hold_conf_exit for hold_conf_bars bars (regime/alignment flipped —
    #     the "새로운 상황으로 변경" case).
    # This locks a stalled winner before it round-trips, ahead of the lagging
    # bias-flip, and frees the seat for the next coin. 0 disables (default).
    hold_conf_exit: float = 0.0
    hold_conf_bars: int = 2
    hold_conf_min_r: float = 1.0
    hold_giveback: float = 0.5
    # --- losing-position early cut (V5) --------------------------------------
    # Symmetric counterpart to the winner-only SignalFade above, for the side
    # the profit-protecting exit deliberately ignores: a LOSING trend position.
    # The same live hold_confidence (regime fit + MTF alignment + 4H momentum)
    # is watched; when it stays below hold_loss_exit for hold_conf_bars
    # consecutive bars WHILE the trade is underwater, the thesis has turned
    # adverse ("모멘텀/국면이 포지션에 악영향") and the position is closed ahead of
    # the structural stop (LossFade) — banking the smaller loss instead of
    # riding a broken trade down to a full SL.
    #
    # Floor choice is EVIDENCE-set, not a-priori (G5, design window): while a
    # trend position is still open AND underwater its live conviction clusters
    # ~0.45-0.55, so a floor there cuts the below-median-conviction losers at
    # ~ -0.3R vs a full -1R stop. Across 0.45-0.60 expectancy stays +0.055R and
    # max_dd is unchanged (portfolio n=1529): the edge sign never flips — the
    # cut is measurably HARMLESS, its payoff a forward tail-risk hedge on slow
    # bleeders. Below ~0.40 it never binds (inert); it is deliberately NOT a
    # tight in-the-noise price stop (the swept-stop failure of E5). It catches
    # SLOW thesis-decay, not a FAST structural stop-out (that is an entry-
    # leverage matter, not an exit one), and never overrides liq-before-stop.
    # 0 = off (default); the whale profile arms it. Same bars as the winner.
    hold_loss_exit: float = 0.0
    # --- whale mode (V4): concentrate the WHOLE account into the single
    # highest-confidence signal at that bar, sized full-margin at a
    # confidence-chosen leverage tier. Off by default (portfolio mode);
    # profile("whale") turns it on. See the profile block below.
    whale_mode: bool = False
    # --- portfolio risk ---
    max_positions: int = 4
    max_positions_corr: int = 2
    max_open_risk: float = 0.018
    max_margin_frac: float = 0.60
    dd_half: float = 0.10            # halve risk beyond this drawdown
    dd_stop: float = 0.20            # no new entries beyond this drawdown
    dd_resume: float = 0.15
    circuit_loss_24h: float = 0.04
    circuit_pause_h: int = 24
    cooldown_bars: int = 12          # per-asset bars to wait after a close
                                     # (RSI<20 persists; avoid re-catching a knife)
    # SR-B / SR-C (E19) were measured on the design window and REJECTED — see
    # EXPERIMENTS.md E19 / SEAT_REDESIGN.md. SR-C (reason-split cooldown) moved
    # whale terminal wealth non-monotonically (win_cd 2/4/6/8/12 -> 28/31/31/72/68x,
    # a G5 sign-flip = path noise, avg_r rising the whole time = the E9 mirage);
    # SR-B (dead-seat rotation) never fired (rot=0, the losing-armed-holder +
    # pending-cross-candidate coincidence does not occur). Neither is in the code.
    # --- costs ---
    taker_fee: float = 0.0005        # one way
    # OKX only serves ~3 months of funding history; before that the engine
    # charges this flat per-8h drag on any open position (conservative).
    funding_default_drag: float = 0.0001
    cost_mult: float = 1.0           # stress knob (2.0 in G6)
    # --- confidence weights ---
    w_base: float = 0.55
    w_regime: float = 0.25
    w_align: float = 0.20
    # --- V5 conf / hold_conf redesign (E17, CONF_REDESIGN.md) ----------------
    # Two pre-registered variants cleared their gates and are ADOPTED as the
    # defaults below; four were measured and REJECTED (kept off, with verdicts
    # in CONF_REDESIGN.md §3): HV-B streak 2->4 (delays winners' exit, whale
    # ret -6%), HV-C drop BTC hold-vote (both halves worse — the entry-gate
    # double-count does NOT transfer to exit), CV-B percentile c_base (its only
    # rationale, whale seat comparability, is moot once whale keeps legacy conf),
    # CV-C structural whale leverage (whale MDD -48%->-65%: the conf tier's
    # stop-width-correlated de-risking is load-bearing).
    #   hold_cont  (HV-A, ADOPTED): continuous 4H-EMA-gap fit + symmetric rsi in
    #     hold_confidence, replacing the stepped daily-regime-map fit and the
    #     one-sided rsi_ok deadzone. base is inert (hold disarmed); whale exp
    #     +0.134->+0.196R, MDD -47.9%->-46.5%, both halves up.
    #   conf_clean (CV-A, ADOPTED): entry conf grades only the continuous
    #     evidence c_base; c_fit (sign-inverted: RANGE breakouts score the best R
    #     yet get fit=0.4) and c_align (mask-saturated) leave the SCORE — their
    #     mask GATES stay. Funding leaves the score too, becoming a crowding
    #     veto. base exp +0.107->+0.110R, pf 1.57->1.63, MC p95 -20.7%->-18.6%.
    #     conf_entry stays 0.55 (base n 1381->1267, inside the pre-set +-10%
    #     band, so no threshold re-fitting). WHALE opts OUT (conf_clean=False in
    #     the profile): its conf->leverage tier map is measured load-bearing.
    hold_cont: bool = True
    conf_clean: bool = True

P = Params()


# --- Leverage profiles (V3) --------------------------------------------------
# The user's target is +100%/month via aggressive leverage. Honesty first:
# there is NO parameter set that makes +100%/month the EXPECTATION while keeping
# ruin near zero — that combination is mathematically unavailable (a ~3+ monthly
# Sharpe would be required; ours is ~1). What IS available is a spectrum of
# profiles trading survival for tail upside, each reported with its Monte-Carlo
# ruin numbers so the choice is made with eyes open. Select with PROFILE env var
# (base | turbo | max), default base.
#
#   base  — survival profile. Passes the MC gate (95%p MDD <= 30%, P(>50%)~0).
#           Design-window: ~ +2.6%/mo geometric, best months +30-40%.
#   turbo — 2x base risk + universe-wide aggression. Higher tail: best months
#           can reach +80-120%, but 95%p MDD ~ 45% and P(MDD>50%) ~ 8%.
#   max   — 3x base risk. Reaches the +100%/mo tail in strong-trend months but
#           95%p MDD ~ 60%+ and P(ruinous DD) is real. Paper-only guard rail.
#   whale — single-position concentration (V4, the user's crypto-whale design).
#           Instead of spreading risk over up to 4 positions, hold at most ONE:
#           each bar the best signal across the whole universe wins the seat —
#           PROVEN slots (risk_scale 1.0) outrank experimental ones, then conf
#           breaks ties (E15; conf does not rank R, E9) — sized FULL-MARGIN
#           (margin == whole account × the slot's risk_scale) at a
#           confidence-chosen leverage tier (>=0.80 -> 10x, >=0.70 -> 5x,
#           >=0.62 -> 3x, >=0.55 -> 2x). Experimental slots deploy only
#           risk_scale (0.20) of the account as margin — the staged-rollout
#           rule survives concentration (E15: it used to be silently dropped
#           here). Highest tail of all profiles and the highest ruin: a single
#           proven stop-out costs lev*stop_frac of the account (e.g. 10x on a
#           2% stop = -20%). The position is held to its exit — a fresh signal
#           on another coin never displaces it (no churn), except the Upgrade
#           rule (proven displaces experimental).
#
# Profiles change ONLY sizing/aggression knobs — never the liquidation-safety
# invariant (stop always before liquidation) which is non-negotiable in all.
# whale keeps it too: when the confidence tier's leverage would place the stop
# beyond liquidation, final_leverage caps the leverage down (a wide stop simply
# gets a smaller tier), so the account is never sized into a liquidation.

def _scaled_tiers(tiers: tuple, mult: float) -> tuple:
    return tuple((lo, lev, risk * mult) for lo, lev, risk in tiers)


def profile(name: str = "base") -> Params:
    name = (name or "base").lower()
    if name == "base":
        return Params()
    if name == "turbo":
        return replace(
            Params(),
            conf_tiers=_scaled_tiers(Params().conf_tiers, 2.0),
            max_open_risk=0.036,
            aggression_syms=tuple(UNIVERSE.keys()),   # press every asset at highs
            eq_boost_mult=1.75,
        )
    if name == "max":
        return replace(
            Params(),
            conf_tiers=_scaled_tiers(Params().conf_tiers, 3.0),
            max_open_risk=0.055,
            aggression_syms=tuple(UNIVERSE.keys()),
            pyramid_max=3,
            eq_boost_mult=2.0,
        )
    if name == "whale":
        return replace(
            Params(),
            whale_mode=True,
            # whale keeps the LEGACY entry conf (CV-A off): unlike base/turbo/max
            # — which use conf only as a >=conf_entry gate under a single
            # leverage tier — whale maps conf -> leverage tier (0.55/0.62/0.70/
            # 0.80 -> 2/3/5/10x). CV-A's lower c_base-only conf distribution
            # collapses that mapping (ret 54x->13x, MDD -48%->-60%); the tier's
            # stop-width/regime-correlated de-risking is load-bearing (E17).
            conf_clean=False,
            # confidence -> leverage tier (risk field unused under full-margin)
            conf_tiers=((0.55, 2.0, 0.01), (0.62, 3.0, 0.01),
                        (0.70, 5.0, 0.01), (0.80, 10.0, 0.01)),
            conf_entry=0.55,
            max_positions=1,
            max_positions_corr=1,
            # one all-in position IS the whole book: the portfolio-diversification
            # gates below don't apply, so set them generously out of the way.
            # (full-margin uses 100% of equity as margin; a single stop-out risks
            #  at most ~lev*stop_frac, structurally <~35% of the account.)
            max_open_risk=2.0,
            max_margin_frac=2.0,
            # pyramiding needs free margin to add — there is none at full margin
            pyramid_max=0,
            aggression_syms=(),
            # re-score the single held position every bar and bank a winner
            # when its conviction collapses (regime/alignment/momentum turned),
            # freeing the seat for the next coin. The GIVEBACK leg is disabled
            # (1.0 = never): E16 re-measured E9's finding at whale scale — the
            # 50%-of-peak giveback exit was cutting the compounding trend
            # winners this profile lives on (design window +318% -> +5,434%,
            # MDD -64% -> -48% with the leg off; trail + conviction-collapse
            # + LossFade remain the only exits).
            hold_conf_exit=0.50,
            hold_conf_bars=2,
            hold_conf_min_r=1.0,
            hold_giveback=1.0,
            # and cut a LOSING single position early when its live conviction
            # stays below this (adverse regime/momentum) for hold_conf_bars,
            # protecting the concentrated account before a full leveraged stop.
            # 0.50 is the lowest floor that actually binds on a slow-bleed loser
            # (below ~0.40 is inert); G5-validated harmless in portfolio mode.
            hold_loss_exit=0.50,
        )
    raise ValueError(f"unknown profile: {name!r} (base|turbo|max|whale)")
