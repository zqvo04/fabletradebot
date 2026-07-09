"""All strategy parameters from BLUEPRINT.md, centralized.

Horizon mapping (day/swing targeting):
  - swing playbooks: P1 (squeeze breakout), P2 (trend pullback) — held up to ~10 days, trailed.
  - day playbooks:   P3 (sweep reversal, <=24h), P4 (funding squeeze, <=72h).
"""
from dataclasses import dataclass, field


def _d(**kw):
    return field(default_factory=lambda: dict(kw))


@dataclass
class Config:
    assets: tuple = ("BTC", "ETH", "SOL", "HYPE")
    majors: tuple = ("BTC", "ETH")  # exempt from BTC macro gate

    # ---- feature windows (1H bars) ----
    er_n: int = 24
    tstat_n: int = 48
    ewma_lam: float = 0.94
    vpct_win: int = 720          # 30d vol percentile
    bbw_n: int = 20
    bbw_k: float = 2.0
    bbwpct_win: int = 2160       # 90d BBW percentile
    volvol_win: int = 48
    volvolpct_win: int = 720
    don_slow: int = 48
    don_fast: int = 24
    swing_win: int = 48
    swing_exclude: int = 2
    atr_n: int = 14
    vol_sma: int = 20
    funding_z_win: int = 90      # 30d of 8h funding periods
    winsor_sigma: float = 5.0
    min_history_bars: int = 720  # 30d minimum history

    # ---- regime thresholds ----
    er_trend: float = 0.35
    tstat_min: float = 2.0
    vpct_crisis: float = 85.0
    ret_sigma_crisis: float = -4.0
    volvol_crisis: float = 90.0
    bbw_squeeze: float = 15.0
    hysteresis: int = 3          # bars to confirm a regime switch (CRISIS is immediate)

    # ---- alpha signal ----
    theta: dict = _d(TREND=0.55, SQUEEZE=0.55, CHOP=0.75)
    # evidence weights [E1 structure, E2 orderflow, E3 positioning, E4 cross, E5 vol-context]
    weights: dict = _d(
        TREND=(0.30, 0.20, 0.15, 0.20, 0.15),
        SQUEEZE=(0.30, 0.25, 0.10, 0.15, 0.20),
        CHOP=(0.30, 0.30, 0.25, 0.15, 0.00),
    )
    # per-playbook (win prob, avg win in R) used by the EV gate; L = 1R
    ev_pw: dict = _d(P1=(0.35, 4.0), P2=(0.40, 2.8), P3=(0.55, 1.6), P4=(0.40, 2.5))
    exp_hold: dict = _d(P1=48, P2=48, P3=12, P4=36)  # bars, for funding-cost estimate
    p4_size: float = 0.5         # P4 trades at half size
    p1_vol_mult: float = 1.5     # breakout volume filter
    p2_funding_hot: float = 1.0  # pullback requires funding cooled below this z
    p4_fz: float = 2.0
    p4_stall_bars: int = 12

    # ---- risk & sizing ----
    r_base: float = 0.0075
    m_regime: dict = _d(TREND_UP=1.25, TREND_DOWN=1.25, SQUEEZE=1.0, CHOP=0.6)
    m_liq: dict = _d(BTC=1.0, ETH=1.0, SOL=0.8, HYPE=0.5)
    lev_cap: dict = _d(BTC=5.0, ETH=5.0, SOL=3.0, HYPE=1.5)
    beta: dict = _d(BTC=1.0, ETH=1.1, SOL=1.5, HYPE=2.0)
    beta_cap: float = 2.0
    max_positions: int = 4
    max_same_dir: int = 3
    perf_win: int = 20
    perf_lo: float = -3.0
    perf_hi: float = 5.0
    m_perf_lo: float = 0.5
    m_perf_hi: float = 1.25
    m_perf_cap: float = 1.5
    daily_stop: float = -0.03
    weekly_stop: float = -0.07
    mdd_stop: float = -0.15
    halt_bars: int = 24

    # ---- costs ----
    fee_bps: float = 5.0
    slip_bps: dict = _d(BTC=2.0, ETH=2.0, SOL=5.0, HYPE=15.0)

    # ---- trade management ----
    partial_at_r: float = 1.0
    partial_frac: float = 0.40
    chandelier_atr: float = 2.75
    pyr_advance_atr: float = 0.5
    time_stop: dict = _d(P1=12, P2=12, P3=8, P4=16)
    time_stop_mfe: float = 0.5   # in R
    max_hold: dict = _d(P1=240, P2=240, P3=24, P4=72)
    reentry_max: int = 2
    reentry_decay: float = 0.7
    reentry_window: int = 48
    cooldown_bars: int = 24
    min_stop_atr: dict = _d(P1=0.5, P2=0.5, P3=0.3, P4=0.5)


def h4_config() -> Config:
    """4H-bar variant. Structure windows (ER, Donchian, swing, t-stat) keep
    their BAR counts (horizons scale x4 into swing tempo); wall-clock windows
    (vol percentiles, holds, time stops) are rescaled to preserve real time.
    Motivation: on 1H bars the ATR-sized stops are so tight that taker fees
    consume the gross edge (see VALIDATION.md); 4H roughly halves cost per R.
    """
    cfg = Config()
    cfg.vpct_win = 180           # 30d of 4H bars
    cfg.bbwpct_win = 540         # 90d
    cfg.volvolpct_win = 180
    cfg.min_history_bars = 180
    cfg.time_stop = dict(P1=6, P2=6, P3=4, P4=8)       # 24h / 16h / 32h
    cfg.max_hold = dict(P1=90, P2=90, P3=6, P4=18)     # 15d swing, 24h/3d day
    cfg.exp_hold = dict(P1=12, P2=12, P3=3, P4=9)      # funding periods est.
    cfg.p4_stall_bars = 3        # 12h
    cfg.cooldown_bars = 6        # 24h
    cfg.reentry_window = 12      # 48h
    cfg.halt_bars = 24           # RiskManager uses hours, unchanged
    return cfg


def test_config() -> Config:
    """Shrunk windows so unit/integration tests warm up quickly."""
    cfg = Config()
    cfg.vpct_win = 120
    cfg.bbwpct_win = 240
    cfg.volvolpct_win = 120
    cfg.funding_z_win = 30
    cfg.min_history_bars = 150
    return cfg
