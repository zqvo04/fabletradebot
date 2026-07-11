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
    conf_entry: float = 0.60
    conf_tiers: tuple = ((0.60, 2.0, 0.005), (0.70, 3.0, 0.008),
                         (0.80, 5.0, 0.011), (0.90, 10.0, 0.015))
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
    # --- S1 pullback in trend ---
    s1_pullback_atr: float = 0.5     # distance to 4H EMA20 in ATR4H
    s1_rsi_lo: float = 35.0
    s1_rsi_hi: float = 55.0
    s1_sl_atr: float = 0.6
    # --- S2 squeeze breakout ---
    s2_bbw_pct: float = 20.0         # BB width percentile threshold
    s2_bbw_lookback: int = 240
    s2_donchian: int = 48
    s2_vol_mult: float = 1.5
    s2_sl_atr: float = 0.6
    s2_sl_min_atr: float = 1.0
    # --- S3 sweep reversal ---
    s3_lookback: int = 48
    s3_sweep_atr: float = 0.25
    s3_wick_frac: float = 0.55
    s3_vol_mult: float = 2.0
    s3_sl_atr: float = 0.5
    # --- S4 funding modifier ---
    funding_z_ext: float = 1.5
    funding_bonus: float = 0.05
    funding_penalty: float = 0.10
    funding_z_window: int = 270      # 90d of 8h fundings
    # --- exits ---
    tp1_r: float = 1.5
    tp1_frac: float = 0.5
    trail_atr: float = 3.0
    time_stop_bars: int = 36
    time_stop_min_r: float = 0.5
    # --- portfolio risk ---
    max_positions: int = 4
    max_positions_corr: int = 2
    max_open_risk: float = 0.045
    max_margin_frac: float = 0.60
    dd_half: float = 0.10            # halve risk beyond this drawdown
    dd_stop: float = 0.20            # no new entries beyond this drawdown
    dd_resume: float = 0.15
    circuit_loss_24h: float = 0.04
    circuit_pause_h: int = 24
    cooldown_bars: int = 4           # per-asset bars to wait after a close
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
