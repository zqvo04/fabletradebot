"""Per-asset setup scanning: features -> S1/S2/S3 candidates -> confidence.

Everything is computed on closed bars only. Row t (1H bar open time) is the
decision made at t+1h, right after that bar closes; a resulting trade is
filled at the NEXT 1H open (engine's job).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Params
from .data_okx import closed_asof_1h, resample
from .indicators import atr, bollinger_width, donchian, ema, pct_rank, rsi, zscore


def build_features(df1h: pd.DataFrame, funding: pd.Series | None, p: Params) -> pd.DataFrame:
    f = pd.DataFrame(index=df1h.index)
    f[["open", "high", "low", "close", "volume"]] = df1h[["open", "high", "low", "close", "volume"]]
    f["atr1h"] = atr(df1h, 14)
    f["rsi1h"] = rsi(df1h["close"], 14)
    f["vol_med"] = df1h["volume"].rolling(48).median()
    f["bbw_pct"] = pct_rank(bollinger_width(df1h["close"], 20, 2.0), p.s2_bbw_lookback)
    f["don_hi"], f["don_lo"] = donchian(df1h, p.s2_donchian)
    rng = (df1h["high"] - df1h["low"]).replace(0, np.nan)
    f["body_up"] = ((df1h["close"] - df1h["open"]) / rng).clip(0, 1)
    f["body_dn"] = ((df1h["open"] - df1h["close"]) / rng).clip(0, 1)
    f["wick_lo"] = ((df1h[["open", "close"]].min(axis=1) - df1h["low"]) / rng).clip(0, 1)
    f["wick_hi"] = ((df1h["high"] - df1h[["open", "close"]].max(axis=1)) / rng).clip(0, 1)
    f["prev_high"] = df1h["high"].shift(1)
    f["prev_low"] = df1h["low"].shift(1)

    d4 = resample(df1h, 4)
    e20, e50 = ema(d4["close"], 20), ema(d4["close"], 50)
    feat4 = pd.DataFrame({"ema20_4h": e20, "atr4h": atr(d4, 14),
                          "bias4h": np.sign(e20 - e50),
                          "align4h": ((e20 - e50).abs() / atr(d4, 14)).clip(0, 1.5) / 1.5})
    f = f.join(closed_asof_1h(feat4, 4, df1h.index))

    d1 = resample(df1h, 24)
    g20, g50, g100 = ema(d1["close"], 20), ema(d1["close"], 50), ema(d1["close"], 100)
    bias1d = pd.Series(0.0, index=d1.index)
    bias1d[(g20 > g50) & (d1["close"] > g100)] = 1.0
    bias1d[(g20 < g50) & (d1["close"] < g100)] = -1.0
    f = f.join(closed_asof_1h(pd.DataFrame({"bias1d": bias1d}), 24, df1h.index))

    if funding is not None and len(funding) >= p.funding_z_window // 3:
        fz = zscore(funding, p.funding_z_window)
        fz.index = fz.index  # funding time == effective time (already settled)
        proj = fz.reindex(fz.index.union(df1h.index + pd.Timedelta(hours=1))).ffill() \
                 .reindex(df1h.index + pd.Timedelta(hours=1))
        proj.index = df1h.index
        f["fund_z"] = proj
    else:
        f["fund_z"] = np.nan
    return f


def _funding_mod(fund_z: pd.Series, direction: int, p: Params) -> pd.Series:
    z = fund_z * direction  # z along the trade direction
    mod = pd.Series(0.0, index=fund_z.index)
    mod[z < -p.funding_z_ext] = p.funding_bonus     # extreme AGAINST us -> contrarian carry
    mod[z > p.funding_z_ext] = -p.funding_penalty   # crowded WITH us -> pay carry
    return mod


def _tf_align(f: pd.DataFrame, btc_dir: pd.Series, direction: int) -> pd.Series:
    return ((f["bias1d"] == direction).astype(float)
            + (f["bias4h"] == direction).astype(float)
            + (btc_dir == direction).astype(float)) / 3.0


def scan(f: pd.DataFrame, regime: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Return candidates: columns dir, conf, sl, setup. At most one per bar
    (highest confidence wins)."""
    state = regime["state"].reindex(f.index).fillna("RANGE")
    btc_dir = regime["btc_dir"].reindex(f.index).fillna(0)
    vol_ratio = (f["volume"] / f["vol_med"]).replace([np.inf, -np.inf], np.nan)
    out = []

    for direction in (1, -1):
        body = f["body_up"] if direction == 1 else f["body_dn"]
        wick = f["wick_lo"] if direction == 1 else f["wick_hi"]
        align = _tf_align(f, btc_dir, direction)
        fmod = _funding_mod(f["fund_z"].fillna(0.0), direction, p)

        # ---- S1 pullback-in-trend ----
        dist = (f["close"] - f["ema20_4h"]) / f["atr4h"] * direction
        rsi_d = f["rsi1h"] if direction == 1 else 100 - f["rsi1h"]
        resume = (f["close"] > f["prev_high"]) if direction == 1 else (f["close"] < f["prev_low"])
        s1 = ((f["bias1d"] == direction) & (f["bias4h"] == direction)
              & state.isin(["TREND", "HIGH_VOL"])
              & dist.between(-1.0, p.s1_pullback_atr)
              & rsi_d.between(p.s1_rsi_lo, p.s1_rsi_hi) & resume & body.notna())
        if direction == 1:
            swing = f["low"].rolling(6).min()
            sl1 = swing - p.s1_sl_atr * f["atr1h"]
        else:
            swing = f["high"].rolling(6).max()
            sl1 = swing + p.s1_sl_atr * f["atr1h"]
        base1 = (f["align4h"] + (1 - dist.abs() / 1.0).clip(0, 1) + body
                 + (vol_ratio / 2).clip(0, 1)) / 4
        fit1 = state.map({"TREND": 1.0, "HIGH_VOL": 0.4}).fillna(0.0)
        out.append(("S1", direction, s1, sl1, base1, fit1, align, fmod))

        # ---- S2 squeeze breakout ----
        level = f["don_hi"] if direction == 1 else f["don_lo"]
        broke = (f["close"] > level) if direction == 1 else (f["close"] < level)
        s2 = ((f["bbw_pct"] < p.s2_bbw_pct) & broke
              & (vol_ratio >= p.s2_vol_mult) & (f["bias4h"] != -direction)
              & state.isin(["TREND", "RANGE"]))
        sl_a = level - direction * p.s2_sl_atr * f["atr1h"]
        sl_b = f["close"] - direction * p.s2_sl_min_atr * f["atr1h"]
        sl2 = pd.concat([sl_a, sl_b], axis=1).min(axis=1) if direction == 1 \
            else pd.concat([sl_a, sl_b], axis=1).max(axis=1)
        margin = ((f["close"] - level) * direction / f["atr1h"]).clip(0, 1)
        base2 = ((1 - f["bbw_pct"] / p.s2_bbw_pct).clip(0, 1) + margin + body
                 + (vol_ratio / 3).clip(0, 1)) / 4
        fit2 = state.map({"TREND": 1.0, "RANGE": 0.6}).fillna(0.0)
        out.append(("S2", direction, s2, sl2, base2, fit2, align, fmod))

        # ---- S3 sweep reversal (counter-trend, RANGE only) ----
        ext = f["low"] if direction == 1 else f["high"]
        swept = ((f["don_lo"] - f["low"]) if direction == 1 else (f["high"] - f["don_hi"]))
        s3 = ((state == "RANGE") & (swept > p.s3_sweep_atr * f["atr1h"])
              & (((f["close"] > f["don_lo"]) & (direction == 1))
                 | ((f["close"] < f["don_hi"]) & (direction == -1)))
              & (wick >= p.s3_wick_frac) & (vol_ratio >= p.s3_vol_mult))
        sl3 = ext - direction * p.s3_sl_atr * f["atr1h"]
        base3 = ((swept / f["atr1h"]).clip(0, 1) + wick + (vol_ratio / 4).clip(0, 1)) / 3
        fit3 = (state == "RANGE").astype(float)
        out.append(("S3", direction, s3, sl3, base3, fit3, align, fmod))

    rows = []
    for name, direction, mask, sl, base, fit, align, fmod in out:
        conf = (p.w_base * base + p.w_regime * fit + p.w_align * align + fmod).clip(0, 1)
        idx = f.index[mask.fillna(False)]
        if len(idx) == 0:
            continue
        sub = pd.DataFrame({"dir": direction, "conf": conf.loc[idx],
                            "sl": sl.loc[idx], "setup": name}, index=idx)
        rows.append(sub)
    if not rows:
        return pd.DataFrame(columns=["dir", "conf", "sl", "setup"])
    cand = pd.concat(rows).sort_values("conf", ascending=False)
    cand = cand[~cand.index.duplicated(keep="first")].sort_index()
    # a valid stop must sit on the correct side of the close
    ok = (cand["sl"] - f["close"].reindex(cand.index)) * cand["dir"] < 0
    return cand[ok & cand["conf"].ge(p.conf_entry)]
