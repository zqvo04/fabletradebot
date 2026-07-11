"""Per-asset setup scanning — V1 signal set AFTER design-window falsification.

Survivors (EXPERIMENTS.md E5-E7):
  CAPREV — capitulation-reversal LONG: 1H RSI(14) < 20. The only edge that held
           in BOTH design half-periods (+1.2% / +2.9% fwd-72h, 11/12 assets).
  BRK    — 7d-high breakout LONG in an aligned uptrend (1D & 4H & BTC). Strong
           in 2023-24 (+3.1%), weak recently (+0.25%) — kept as secondary with
           strict gating; expected to contribute little.
Rejected by measurement (kept out of code, documented in EXPERIMENTS.md):
  pullback-resumption (S1, -0.08R best), sweep reversal (S3, -0.12R),
  squeeze filter (S2, unstable), ALL short-side variants (breakdown short:
  -3.1% against, 12/12 assets in the recent half; overbought short: -1.9%).
V1 is therefore long-only; in a bear regime it stands aside by construction.

Everything is computed on closed bars only. Row t (1H bar open time) is the
decision made at t+1h; the engine fills at the NEXT 1H open.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Params
from .data_okx import closed_asof_1h, resample
from .indicators import atr, bollinger_width, ema, pct_rank, rsi, zscore


def build_features(df1h: pd.DataFrame, funding: pd.Series | None, p: Params) -> pd.DataFrame:
    f = pd.DataFrame(index=df1h.index)
    f[["open", "high", "low", "close", "volume"]] = df1h[["open", "high", "low", "close", "volume"]]
    f["atr1h"] = atr(df1h, 14)
    f["rsi1h"] = rsi(df1h["close"], 14)
    f["vol_med"] = df1h["volume"].rolling(48).median()
    f["bbw_pct"] = pct_rank(bollinger_width(df1h["close"], 20, 2.0), p.bbw_lookback)
    f["hh"] = df1h["high"].rolling(p.brk_lookback).max().shift(1)
    f["swing_lo"] = df1h["low"].rolling(6).min()
    rng = (df1h["high"] - df1h["low"]).replace(0, np.nan)
    f["body_up"] = ((df1h["close"] - df1h["open"]) / rng).clip(0, 1)
    f["wick_lo"] = ((df1h[["open", "close"]].min(axis=1) - df1h["low"]) / rng).clip(0, 1)

    d4 = resample(df1h, 4)
    e20, e50 = ema(d4["close"], 20), ema(d4["close"], 50)
    feat4 = pd.DataFrame({"atr4h": atr(d4, 14), "bias4h": np.sign(e20 - e50)})
    f = f.join(closed_asof_1h(feat4, 4, df1h.index))

    d1 = resample(df1h, 24)
    g20, g50, g100 = ema(d1["close"], 20), ema(d1["close"], 50), ema(d1["close"], 100)
    bias1d = pd.Series(0.0, index=d1.index)
    bias1d[(g20 > g50) & (d1["close"] > g100)] = 1.0
    bias1d[(g20 < g50) & (d1["close"] < g100)] = -1.0
    f = f.join(closed_asof_1h(pd.DataFrame({"bias1d": bias1d}), 24, df1h.index))

    if funding is not None and len(funding) >= p.funding_z_window // 3:
        fz = zscore(funding, p.funding_z_window)
        proj = fz.reindex(fz.index.union(df1h.index + pd.Timedelta(hours=1))).ffill() \
                 .reindex(df1h.index + pd.Timedelta(hours=1))
        proj.index = df1h.index
        f["fund_z"] = proj
    else:
        f["fund_z"] = np.nan
    return f


def scan(f: pd.DataFrame, regime: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Long-only candidates: columns dir, conf, sl, setup, c_* components.
    At most one per bar (highest confidence wins)."""
    state = regime["state"].reindex(f.index).fillna("RANGE")
    btc_dir = regime["btc_dir"].reindex(f.index).fillna(0)
    vol_ratio = (f["volume"] / f["vol_med"]).replace([np.inf, -np.inf], np.nan)
    # funding modifier for a LONG: shorts crowded (z << 0) is squeeze fuel;
    # longs crowded (z >> 0) pays carry into a crowded trade
    z = f["fund_z"].fillna(0.0)
    fmod = pd.Series(0.0, index=f.index)
    fmod[z < -p.funding_z_ext] = p.funding_bonus
    fmod[z > p.funding_z_ext] = -p.funding_penalty

    out = []

    # ---- CAPREV: capitulation-reversal long (disabled by default — E8) ----
    oversold_now = (f["rsi1h"] < p.cap_rsi) if p.cap_enabled \
        else pd.Series(False, index=f.index)
    if p.cap_confirm:
        # capitulated within the last N bars AND this bar confirms the bounce
        was_oversold = f["rsi1h"].shift(1).rolling(p.cap_confirm_bars).min() < p.cap_rsi
        confirm = (f["close"] > f["high"].shift(1)) & (f["body_up"] > 0.5)
        mask = was_oversold & confirm & (vol_ratio >= p.cap_vol_mult)
        rsi_ref = f["rsi1h"].shift(1).rolling(p.cap_confirm_bars).min()
        swing = f["low"].rolling(p.cap_confirm_bars + 1).min()   # capitulation low
    else:
        mask = oversold_now & (vol_ratio >= p.cap_vol_mult)
        rsi_ref = f["rsi1h"]
        swing = f["swing_lo"]
    depth = ((p.cap_rsi - rsi_ref) / p.cap_rsi).clip(0, 1)
    base = (depth + f["wick_lo"].fillna(0) + (vol_ratio / 3).clip(0, 1)) / 3
    fit = state.map({"RANGE": 1.0, "TREND": 0.8, "HIGH_VOL": 0.6}).fillna(0.0)
    # bounce quality stands in for trend alignment (counter-trend by nature)
    align = ((vol_ratio / 2).clip(0, 1) + f["wick_lo"].fillna(0)) / 2
    sl = swing - p.sl_swing_atr * f["atr1h"]
    if p.cap_floor_atr > 0:
        sl = np.minimum(sl, f["close"] - p.cap_floor_atr * f["atr1h"])
    out.append(("CAPREV", mask & p.cap_enabled, sl, base, fit, align, fmod))

    # ---- BRK: aligned 7d-high breakout long ----
    mask = ((f["close"] > f["hh"]) & (f["bias1d"] == 1) & (f["bias4h"] == 1)
            & (btc_dir == 1) & (vol_ratio >= p.brk_vol_mult)
            & state.isin(["TREND", "RANGE"]))
    margin = ((f["close"] - f["hh"]) / f["atr1h"]).clip(0, 1)
    squeeze = (1 - f["bbw_pct"] / 100).clip(0, 1)   # tighter pre-break = better
    base = (margin + squeeze + f["body_up"].fillna(0) + (vol_ratio / 3).clip(0, 1)) / 4
    fit = state.map({"TREND": 1.0, "RANGE": 0.4}).fillna(0.0)
    align = ((f["bias1d"] == 1).astype(float) + (f["bias4h"] == 1).astype(float)
             + (btc_dir == 1).astype(float)) / 3
    sl = f["hh"] - p.sl_swing_atr * f["atr1h"]
    out.append(("BRK", mask, sl, base, fit, align, fmod))

    rows = []
    for name, mask, sl, base, fit, align, fm in out:
        conf = (p.w_base * base + p.w_regime * fit + p.w_align * align + fm).clip(0, 1)
        idx = f.index[mask.fillna(False)]
        if len(idx) == 0:
            continue
        rows.append(pd.DataFrame({
            "dir": 1, "conf": conf.loc[idx], "sl": sl.loc[idx], "setup": name,
            "c_base": base.loc[idx], "c_fit": fit.loc[idx],
            "c_align": align.loc[idx], "c_fund": fm.loc[idx]}, index=idx))
    cols = ["dir", "conf", "sl", "setup", "c_base", "c_fit", "c_align", "c_fund"]
    if not rows:
        return pd.DataFrame(columns=cols)
    cand = pd.concat(rows).sort_values("conf", ascending=False)
    cand = cand[~cand.index.duplicated(keep="first")].sort_index()
    # volatility floor: never place a stop inside sl_floor_atr * ATR of price
    close_c = f["close"].reindex(cand.index)
    floor = close_c - cand["dir"] * p.sl_floor_atr * f["atr1h"].reindex(cand.index)
    cand["sl"] = np.where(cand["dir"] == 1, np.minimum(cand["sl"], floor),
                          np.maximum(cand["sl"], floor))
    ok = (cand["sl"] - close_c) * cand["dir"] < 0
    return cand[ok & cand["conf"].ge(p.conf_entry)]
