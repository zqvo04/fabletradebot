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
    conf_tiers: tuple = ((0.55, 5.0, 0.0055),)
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
        "TREND": 10.0, "RANGE": 5.0, "HIGH_VOL": 3.0, "CRISIS": 0.0})
    vol_pct_highvol: float = 80.0
    vol_pct_crisis: float = 90.0
    crash_5d: float = -0.12
    crash_1d: float = -0.07
    hysteresis_bars: int = 2         # 1D bars to confirm a regime switch
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
        # RCL: trend-pullback reclaim — closed 4H bar crosses BACK ABOVE the
        # 4H EMA20 while the 1D trend agrees (mirror short). 1H bar triggers.
        "RCL_L":   {"enabled": True,  "dir": 1, "risk_scale": 0.25},
        "RCL_S":   {"enabled": True,  "dir": -1, "risk_scale": 0.25},
        # OSC: oscillator re-cross (the user-anchor trigger) — RSI(14,4H)
        # crosses back up through 30 -> long / back down through 70 -> short.
        # Mean-reversion style: fixed target + time stop. RANGE + HIGH_VOL.
        "OSC_L":   {"enabled": True,  "dir": 1, "risk_scale": 0.25,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        "OSC_S":   {"enabled": True,  "dir": -1, "risk_scale": 0.25,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        # BND: Bollinger band re-entry — close crosses back INSIDE the 2-sigma
        # band after closing outside it; fade toward value. RANGE only.
        "BND_L":   {"enabled": True,  "dir": 1, "risk_scale": 0.25,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        "BND_S":   {"enabled": True,  "dir": -1, "risk_scale": 0.25,
                    "tp_r": 1.5, "tp_frac": 1.0, "time_stop_bars": 48,
                    "trail_atr": 0.0, "biasflip_exit": False},
        # swing trend-following, short: backtest-rejected (12/12 against, E6)
        # but part of the complete matrix — paper-only at reduced risk, must
        # earn size from the forward track (V2 decision, E12)
        "BRK_S":   {"enabled": True, "dir": -1, "risk_scale": 0.25},
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
    # --- portfolio risk ---
    max_positions: int = 4
    max_positions_corr: int = 2
    max_open_risk: float = 0.025
    max_margin_frac: float = 0.60
    dd_half: float = 0.10            # halve risk beyond this drawdown
    dd_stop: float = 0.20            # no new entries beyond this drawdown
    dd_resume: float = 0.15
    circuit_loss_24h: float = 0.04
    circuit_pause_h: int = 24
    cooldown_bars: int = 12          # per-asset bars to wait after a close
                                     # (RSI<20 persists; avoid re-catching a knife)
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

P = Params()
