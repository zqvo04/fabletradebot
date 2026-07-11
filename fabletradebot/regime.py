"""Global market regime from BTC 1D bars, with hysteresis, plus the
cross-sectional correlation alert. All series are then projected onto the 1H
decision grid via data_okx.closed_asof_1h (no lookahead).

States: TREND (with direction +1/-1), RANGE, HIGH_VOL, CRISIS.
"""
from __future__ import annotations

import pandas as pd

from .config import Params
from .indicators import atr, ema, pct_rank, realized_vol

STATES = ("TREND", "RANGE", "HIGH_VOL", "CRISIS")


def raw_regime_1d(btc_1d: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Per-1D-bar instantaneous state (before hysteresis) + trend direction."""
    c = btc_1d["close"]
    e20, e50, e100 = ema(c, 20), ema(c, 50), ema(c, 100)
    a = atr(btc_1d, 14)
    vol_pct = pct_rank(realized_vol(c, 20), 365)
    r5 = c.pct_change(5)
    r1 = c.pct_change(1)

    crash = (r5 < p.crash_5d) | (r1 < p.crash_1d)
    crisis = crash | ((vol_pct > p.vol_pct_crisis) & (r5 < -0.08))
    high_vol = vol_pct > p.vol_pct_highvol
    trending = ((e20 - e50).abs() / a > 0.5) & (
        ((c > e100) & (e20 > e50)) | ((c < e100) & (e20 < e50)))

    state = pd.Series("RANGE", index=btc_1d.index)
    state[trending] = "TREND"
    state[high_vol] = "HIGH_VOL"
    state[crisis] = "CRISIS"
    direction = pd.Series(0, index=btc_1d.index)
    direction[(e20 > e50) & (c > e100)] = 1
    direction[(e20 < e50) & (c < e100)] = -1
    return pd.DataFrame({"raw_state": state, "btc_dir": direction})


def apply_hysteresis(raw: pd.Series, confirm: int) -> pd.Series:
    """Switch only after `confirm` consecutive bars of the new state.
    CRISIS engages immediately and needs confirm+1 bars to release."""
    out, cur, cand, streak = [], "RANGE", None, 0
    for s in raw.fillna("RANGE"):
        if s == "CRISIS" and cur != "CRISIS":
            cur, cand, streak = "CRISIS", None, 0
        elif s != cur:
            need = confirm + 1 if cur == "CRISIS" else confirm
            if s == cand:
                streak += 1
            else:
                cand, streak = s, 1
            if streak >= need:
                cur, cand, streak = s, None, 0
        else:
            cand, streak = None, 0
        out.append(cur)
    return pd.Series(out, index=raw.index)


def regime_1d(btc_1d: pd.DataFrame, p: Params) -> pd.DataFrame:
    raw = raw_regime_1d(btc_1d, p)
    return pd.DataFrame({"state": apply_hysteresis(raw["raw_state"], p.hysteresis_bars),
                         "btc_dir": raw["btc_dir"]})


def corr_alert_1h(closes_1h: pd.DataFrame, p: Params) -> pd.Series:
    """True when mean pairwise correlation of universe 1H returns spikes.
    Row t uses returns up to and including bar t."""
    rets = closes_1h.pct_change()
    n = rets.shape[1]
    if n < 2:
        return pd.Series(False, index=closes_1h.index)
    # mean pairwise corr from the variance of the cross-sectional mean:
    # var(mean) = (1/n^2) * (sum var + sum_{i!=j} cov). With standardized
    # returns this collapses to mean_corr = (n*var(mean_z) - 1)/(n - 1).
    z = (rets - rets.rolling(p.corr_window_h).mean()) / rets.rolling(p.corr_window_h).std(ddof=0)
    mean_z = z.mean(axis=1, skipna=False)
    var_mean = mean_z.rolling(p.corr_window_h).var(ddof=0)
    mean_corr = (n * var_mean - 1) / (n - 1)
    return (mean_corr > p.corr_alert).fillna(False)
