"""Alpha Signal Logic: evidence vector, playbooks P1-P4, composite score Z.

Playbooks (regime -> setup):
  P1 Squeeze Breakout   (SQUEEZE, swing)  — main profit engine
  P2 Trend Pullback     (TREND,   swing)
  P3 Sweep Reversal     (CHOP,    day)
  P4 Funding Squeeze    (any non-CRISIS, day)
"""
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .regime import TREND_UP, TREND_DOWN, SQUEEZE, CHOP, CRISIS, WARMUP

SWING = "swing"
DAY = "day"


@dataclass
class Signal:
    asset: str
    direction: int          # +1 long, -1 short
    playbook: str           # P1..P4
    entry: float            # reference price (bar close)
    stop: float
    z: float
    horizon: str            # "day" | "swing"
    size_mult: float = 1.0
    targets: list = field(default_factory=list)  # P3/P4 fixed targets
    evidence: dict = field(default_factory=dict)


def _nan(*vals) -> bool:
    return any(v is None or (isinstance(v, float) and np.isnan(v)) for v in vals)


def _theta_key(regime: str) -> str:
    return "TREND" if regime in (TREND_UP, TREND_DOWN) else regime


# ---------------- evidence E2..E5 (E1 is playbook-specific) ----------------

def _e2_orderflow(A, i, d) -> float:
    raw = A["e2raw"][i]
    if _nan(raw):
        return 0.5
    return float(np.clip(0.5 + 0.5 * d * raw, 0.0, 1.0))


def _e3_positioning(A, i, d) -> float:
    fz = A["f_z"][i]
    if _nan(fz):
        return 0.5
    x = d * fz  # >0: crowding on OUR side (headwind), <0: crowd against us (fuel)
    return float(np.clip((2.0 - x) / 3.0, 0.0, 1.0))  # x>=2 -> 0, x<=-1 -> 1


def _e4_cross(btc_ctx, d) -> float:
    if btc_ctx is None:  # majors: neutral
        return 0.5
    e = 0.75 if d * btc_ctx["ret4h"] > 0 else 0.25
    if btc_ctx["regime"] == (TREND_UP if d > 0 else TREND_DOWN):
        e += 0.25
    elif btc_ctx["regime"] == (TREND_DOWN if d > 0 else TREND_UP):
        e -= 0.25
    return float(np.clip(e, 0.0, 1.0))


def _e5_volcontext(A, i) -> float:
    bbwp, bbw, bbw4 = A["bbw_pct"][i], A["bbw"][i], A["bbw"][i - 4] if i >= 4 else np.nan
    if _nan(bbwp, bbw, bbw4):
        return 0.3
    if bbwp <= 25 and bbw > bbw4:
        return 1.0
    return 0.6 if bbwp <= 40 else 0.3


def _score(e1, A, i, d, btc_ctx, regime, cfg: Config) -> tuple[float, dict]:
    ev = {
        "E1": e1,
        "E2": _e2_orderflow(A, i, d),
        "E3": _e3_positioning(A, i, d),
        "E4": _e4_cross(btc_ctx, d),
        "E5": _e5_volcontext(A, i),
    }
    w = cfg.weights[_theta_key(regime)]
    z = float(sum(wi * ei for wi, ei in zip(w, ev.values())))
    return z, ev


def _clamp_stop(entry, stop, atr_, d, min_atr, max_atr=None) -> float:
    """Stop at least min_atr (and optionally at most max_atr) away from entry."""
    dist = d * (entry - stop)
    dist = max(dist, min_atr * atr_)
    if max_atr is not None:
        dist = min(dist, max_atr * atr_)
    return entry - d * dist


# ---------------- playbooks ----------------

def _p1_squeeze_breakout(A, i, regime, btc_ctx, cfg: Config):
    if regime not in cfg.p1_regimes:
        return []
    c, v, atr_ = A["close"][i], A["volume"][i], A["atr"][i]
    hi, lo = A["don_hi"][i], A["don_lo"][i]
    box_hi, box_lo = A["don_hi_f"][i], A["don_lo_f"][i]
    vsma = A["vol_sma"][i]
    if _nan(c, v, atr_, hi, lo, box_hi, box_lo, vsma) or v < cfg.p1_vol_mult * vsma:
        return []
    out = []
    for d, brk, box in ((1, hi, box_lo), (-1, lo, box_hi)):
        if d * (c - brk) <= 0:
            continue
        if cfg.p1_trend_filter:
            ema100 = A["ema100"][i]
            if _nan(ema100) or d * (c - ema100) <= 0:
                continue
        rng_exp = (A["high"][i] - A["low"][i]) >= 1.5 * atr_
        strong_close = d * (c - brk) >= 0.25 * atr_
        e1 = min(0.4 + 0.3 * rng_exp + 0.3 * strong_close, 1.0)
        stop = box if d * (c - box) > 0 else c - d * 1.5 * atr_
        stop = _clamp_stop(c, stop, atr_, d, cfg.min_stop_atr["P1"], 1.5)
        out.append(("P1", d, e1, stop, [], 1.0, SWING))
    return out


def _p2_trend_pullback(A, i, regime, btc_ctx, cfg: Config):
    if regime not in (TREND_UP, TREND_DOWN):
        return []
    d = 1 if regime == TREND_UP else -1
    c, atr_ = A["close"][i], A["atr"][i]
    if _nan(c, atr_) or i < 5:
        return []
    fz = A["f_z"][i]
    if not _nan(fz) and d * fz >= cfg.p2_funding_hot:  # crowding not cooled yet
        return []
    ema = A["ema20"][i - 4 : i + 1]
    if d > 0:
        lows = A["low"][i - 4 : i + 1]
        touched = bool(np.any(lows <= ema * 1.002))
        extreme = float(np.min(lows))
        resumed = c > A["high"][i - 1]
    else:
        highs = A["high"][i - 4 : i + 1]
        touched = bool(np.any(highs >= ema * 0.998))
        extreme = float(np.max(highs))
        resumed = c < A["low"][i - 1]
    if not (touched and resumed):
        return []
    body_ok = d * (c - A["open"][i]) > 0
    vol_ok = A["volume"][i] >= A["vol_sma"][i] if not _nan(A["vol_sma"][i]) else False
    e1 = min(0.6 + 0.2 * body_ok + 0.2 * vol_ok, 1.0)
    stop = _clamp_stop(c, extreme - d * 0.5 * atr_, atr_, d, cfg.min_stop_atr["P2"])
    return [("P2", d, e1, stop, [], 1.0, SWING)]


def _p3_sweep_reversal(A, i, regime, btc_ctx, cfg: Config):
    if regime != CHOP or i < 1:
        return []
    c, atr_ = A["close"][i], A["atr"][i]
    s_hi, s_lo = A["swing_hi"][i], A["swing_lo"][i]
    if _nan(c, atr_, s_hi, s_lo):
        return []
    out = []
    # long: sweep below swing low, close back above it
    pen_lo = s_lo - min(A["low"][i], A["low"][i - 1])
    if pen_lo > 0 and c > s_lo:
        e1 = 0.5 + 0.5 * min(pen_lo / atr_, 1.0)
        stop = _clamp_stop(c, min(A["low"][i], A["low"][i - 1]) - 0.1 * atr_, atr_, 1,
                           cfg.min_stop_atr["P3"])
        mid = (s_hi + s_lo) / 2.0
        if mid > c:
            out.append(("P3", 1, e1, stop, [mid, s_hi], 1.0, DAY))
    # short: sweep above swing high, close back below it
    pen_hi = max(A["high"][i], A["high"][i - 1]) - s_hi
    if pen_hi > 0 and c < s_hi:
        e1 = 0.5 + 0.5 * min(pen_hi / atr_, 1.0)
        stop = _clamp_stop(c, max(A["high"][i], A["high"][i - 1]) + 0.1 * atr_, atr_, -1,
                           cfg.min_stop_atr["P3"])
        mid = (s_hi + s_lo) / 2.0
        if mid < c:
            out.append(("P3", -1, e1, stop, [mid, s_lo], 1.0, DAY))
    return out


def _p4_funding_squeeze(A, i, regime, btc_ctx, cfg: Config):
    n = cfg.p4_stall_bars
    if i < n:
        return []
    c, atr_, fz = A["close"][i], A["atr"][i], A["f_z"][i]
    if _nan(c, atr_, fz) or abs(fz) < cfg.p4_fz:
        return []
    d = -1 if fz > 0 else 1  # fade the crowded side
    progress = -d * (c - A["close"][i - n])  # crowd-direction progress
    if progress >= 0.5 * atr_:
        return []  # crowd is still winning; no stall, no trade
    e1 = min(abs(fz) / 3.0, 1.0)
    stop = c - d * 2.0 * atr_
    target = c + d * 6.0 * atr_  # 3R on the 2-ATR stop
    return [("P4", d, e1, stop, [target], cfg.p4_size, DAY)]


_PLAYBOOKS = (_p1_squeeze_breakout, _p2_trend_pullback, _p3_sweep_reversal, _p4_funding_squeeze)


def generate(asset: str, A: dict, i: int, cfg: Config, btc_ctx: dict | None) -> list[Signal]:
    """All playbook signals for one asset at bar i that clear theta(regime).
    `A` is a dict of numpy feature arrays; `btc_ctx` is None for majors."""
    regime = A["regime"][i]
    if regime in (WARMUP, CRISIS) or i < 13:
        return []
    theta = cfg.theta[_theta_key(regime)]
    signals = []
    for pb in _PLAYBOOKS:
        for name, d, e1, stop, targets, smult, horizon in pb(A, i, regime, btc_ctx, cfg):
            if name not in cfg.playbooks:
                continue
            if name == "P1" and cfg.p1_own_weights:
                z, ev = _score(e1, A, i, d, btc_ctx, SQUEEZE, cfg)
                if z < cfg.theta["SQUEEZE"]:
                    continue
            else:
                z, ev = _score(e1, A, i, d, btc_ctx, regime, cfg)
                if z < theta:
                    continue
            signals.append(Signal(
                asset=asset, direction=d, playbook=name, entry=float(A["close"][i]),
                stop=float(stop), z=z, horizon=horizon, size_mult=smult,
                targets=[float(t) for t in targets], evidence=ev,
            ))
    return signals
