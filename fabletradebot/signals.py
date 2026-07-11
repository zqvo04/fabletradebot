"""Per-asset setup scanning — playbook-matrix architecture (V1.2, E11).

Every entry structure is a PLAYBOOK SLOT in config.Params.playbooks with its
own enable flag, direction, gates and exit overrides. The engine is fully
direction-symmetric; a slot may only be enabled when its edge survived BOTH
design half-periods after costs.

Measured status (design window 2023-06..2026-01, details EXPERIMENTS.md):
  BRK_L  swing trend breakout long ..... ENABLED  (the survivor, E6/E9)
  BRK_S  swing trend breakdown short ... rejected (12/12 assets against, E6)
  FADE_L/FADE_S day pullback fades ..... rejected (sign flip / sub-cost, E11)
  RANGE_L/RANGE_S day range fades ...... rejected (sign flip / negative, E11)
  CAPREV capitulation reversal ......... rejected (catastrophic MAE, E8)

Everything is computed on closed bars only. Row t (1H bar open time) is the
decision made at t+1h; the engine fills at the NEXT 1H open.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Params
from .data_okx import closed_asof_1h, resample
from .indicators import atr, bollinger_width, ema, pct_rank, rsi, zscore

CAND_COLS = ["dir", "conf", "sl", "setup", "c_base", "c_fit", "c_align", "c_fund"]


def build_features(df1h: pd.DataFrame, funding: pd.Series | None, p: Params) -> pd.DataFrame:
    f = pd.DataFrame(index=df1h.index)
    f[["open", "high", "low", "close", "volume"]] = df1h[["open", "high", "low", "close", "volume"]]
    f["atr1h"] = atr(df1h, 14)
    f["rsi1h"] = rsi(df1h["close"], 14)
    f["vol_med"] = df1h["volume"].rolling(48).median()
    f["bbw_pct"] = pct_rank(bollinger_width(df1h["close"], 20, 2.0), p.bbw_lookback)
    f["hh"] = df1h["high"].rolling(p.brk_lookback).max().shift(1)
    f["ll"] = df1h["low"].rolling(p.brk_lookback).min().shift(1)
    f["swing_lo"] = df1h["low"].rolling(6).min()
    f["swing_hi"] = df1h["high"].rolling(6).max()
    hi_r = df1h["high"].rolling(p.range_lookback).max()
    lo_r = df1h["low"].rolling(p.range_lookback).min()
    f["range_pos"] = (df1h["close"] - lo_r) / (hi_r - lo_r).replace(0, np.nan)
    rng = (df1h["high"] - df1h["low"]).replace(0, np.nan)
    f["body_up"] = ((df1h["close"] - df1h["open"]) / rng).clip(0, 1)
    f["body_dn"] = ((df1h["open"] - df1h["close"]) / rng).clip(0, 1)
    f["wick_lo"] = ((df1h[["open", "close"]].min(axis=1) - df1h["low"]) / rng).clip(0, 1)
    f["wick_hi"] = ((df1h["high"] - df1h[["open", "close"]].max(axis=1)) / rng).clip(0, 1)

    d4 = resample(df1h, 4)
    e20, e50 = ema(d4["close"], 20), ema(d4["close"], 50)
    feat4 = pd.DataFrame({"atr4h": atr(d4, 14), "bias4h": np.sign(e20 - e50),
                          "ema20_4h": e20})
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


def _fund_mod(f: pd.DataFrame, d: int, p: Params) -> pd.Series:
    """Crowding along the trade direction pays carry; crowding against is fuel."""
    z = f["fund_z"].fillna(0.0) * d
    mod = pd.Series(0.0, index=f.index)
    mod[z < -p.funding_z_ext] = p.funding_bonus
    mod[z > p.funding_z_ext] = -p.funding_penalty
    return mod


def _tf_align(f: pd.DataFrame, btc_dir: pd.Series, d: int) -> pd.Series:
    return ((f["bias1d"] == d).astype(float) + (f["bias4h"] == d).astype(float)
            + (btc_dir == d).astype(float)) / 3.0


def _playbook(name: str, d: int, f: pd.DataFrame, state: pd.Series,
              btc_dir: pd.Series, vol_ratio: pd.Series, p: Params):
    """Returns (mask, sl, base, fit, align) for one playbook slot."""
    body = f["body_up"] if d == 1 else f["body_dn"]
    wick_against = f["wick_lo"] if d == 1 else f["wick_hi"]
    family = name.split("_")[0]

    if family == "BRK":     # swing trend continuation
        level = f["hh"] if d == 1 else f["ll"]
        broke = (f["close"] > level) if d == 1 else (f["close"] < level)
        mask = (broke & (f["bias1d"] == d) & (f["bias4h"] == d)
                & (btc_dir == d) & (vol_ratio >= p.brk_vol_mult)
                & state.isin(["TREND", "RANGE"]))
        sl = level - d * p.sl_swing_atr * f["atr1h"]
        margin = ((f["close"] - level) * d / f["atr1h"]).clip(0, 1)
        squeeze = (1 - f["bbw_pct"] / 100).clip(0, 1)
        base = (margin + squeeze + body.fillna(0) + (vol_ratio / 3).clip(0, 1)) / 4
        fit = state.map({"TREND": 1.0, "RANGE": 0.4}).fillna(0.0)
        align = _tf_align(f, btc_dir, d)

    elif family == "FADE":  # day-trade pullback fade at the 4H EMA20
        zone = (f["close"] - f["ema20_4h"]) / f["atr4h"] * d
        mask = ((f["bias1d"] == d) & (f["bias4h"] == d) & (state == "TREND")
                & zone.between(-p.fade_zone_atr, 0.2)
                & (body > 0) & (wick_against >= p.fade_wick)
                & (vol_ratio >= 1.0))
        ext = f["low"] if d == 1 else f["high"]
        sl = ext - d * p.sl_swing_atr * f["atr1h"]
        base = (wick_against + body.fillna(0) + (vol_ratio / 3).clip(0, 1)) / 3
        fit = (state == "TREND").astype(float)
        align = _tf_align(f, btc_dir, d)

    elif family == "RANGE":  # day-trade range-edge fade (no 1D trend)
        edge = (f["range_pos"] < p.range_edge) if d == 1 \
            else (f["range_pos"] > 1 - p.range_edge)
        mask = ((f["bias1d"] == 0) & (state == "RANGE") & edge
                & (body > 0) & (wick_against >= p.fade_wick))
        ext = f["low"] if d == 1 else f["high"]
        sl = ext - d * p.sl_swing_atr * f["atr1h"]
        depth = ((p.range_edge - f["range_pos"]) / p.range_edge).clip(0, 1) if d == 1 \
            else ((f["range_pos"] - (1 - p.range_edge)) / p.range_edge).clip(0, 1)
        base = (depth + wick_against + (vol_ratio / 3).clip(0, 1)) / 3
        fit = (state == "RANGE").astype(float)
        align = pd.Series(0.5, index=f.index)   # counter-trend by nature

    else:
        raise ValueError(f"unknown playbook family: {name}")
    return mask, sl, base, fit, align


def scan(f: pd.DataFrame, regime: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Enabled-playbook candidates; at most one per bar (highest conf wins)."""
    state = regime["state"].reindex(f.index).fillna("RANGE")
    btc_dir = regime["btc_dir"].reindex(f.index).fillna(0)
    vol_ratio = (f["volume"] / f["vol_med"]).replace([np.inf, -np.inf], np.nan)

    rows = []
    for name, pb in p.playbooks.items():
        if not pb.get("enabled", False):
            continue
        d = int(pb["dir"])
        mask, sl, base, fit, align = _playbook(name, d, f, state, btc_dir,
                                               vol_ratio, p)
        fm = _fund_mod(f, d, p)
        conf = (p.w_base * base + p.w_regime * fit + p.w_align * align + fm).clip(0, 1)
        idx = f.index[mask.fillna(False)]
        if len(idx) == 0:
            continue
        rows.append(pd.DataFrame({
            "dir": d, "conf": conf.loc[idx], "sl": sl.loc[idx], "setup": name,
            "c_base": base.loc[idx], "c_fit": fit.loc[idx],
            "c_align": align.loc[idx], "c_fund": fm.loc[idx]}, index=idx))
    if not rows:
        return pd.DataFrame(columns=CAND_COLS)
    cand = pd.concat(rows).sort_values("conf", ascending=False)
    cand = cand[~cand.index.duplicated(keep="first")].sort_index()
    # volatility floor: never place a stop inside sl_floor_atr * ATR of price
    close_c = f["close"].reindex(cand.index)
    floor = close_c - cand["dir"] * p.sl_floor_atr * f["atr1h"].reindex(cand.index)
    cand["sl"] = np.where(cand["dir"] == 1, np.minimum(cand["sl"], floor),
                          np.maximum(cand["sl"], floor))
    ok = (cand["sl"] - close_c) * cand["dir"] < 0
    return cand[ok & cand["conf"].ge(p.conf_entry)]
