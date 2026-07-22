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
    f["prev_high"] = df1h["high"].shift(1)
    f["prev_low"] = df1h["low"].shift(1)
    f["body_up"] = ((df1h["close"] - df1h["open"]) / rng).clip(0, 1)
    f["body_dn"] = ((df1h["open"] - df1h["close"]) / rng).clip(0, 1)
    f["wick_lo"] = ((df1h[["open", "close"]].min(axis=1) - df1h["low"]) / rng).clip(0, 1)
    f["wick_hi"] = ((df1h["high"] - df1h[["open", "close"]].max(axis=1)) / rng).clip(0, 1)

    # ---- 4H layer: the PRIMARY decision timeframe (V2). Setups are detected
    # on closed 4H bars; projecting a fired event onto the 1H grid keeps it
    # visible ("armed") for exactly the following 4 hours, during which the
    # 1H layer may pull the precision trigger. 1D stays the reference bias.
    d4 = resample(df1h, 4)
    c4 = d4["close"]
    e20, e50 = ema(c4, 20), ema(c4, 50)
    rsi4 = rsi(c4, 14)
    mid4 = c4.rolling(20).mean()
    sd4 = c4.rolling(20).std(ddof=0)
    bb_lo4, bb_hi4 = mid4 - p.bb_k * sd4, mid4 + p.bb_k * sd4
    feat4 = pd.DataFrame({
        "atr4h": atr(d4, 14), "bias4h": np.sign(e20 - e50), "ema20_4h": e20,
        "ema50_4h": e50, "rsi4h": rsi4,
        # event flags (crossings on closed 4H bars — never levels)
        "osc_up": (rsi4.shift(1) < p.osc_lo) & (rsi4 >= p.osc_lo),
        "osc_dn": (rsi4.shift(1) > p.osc_hi) & (rsi4 <= p.osc_hi),
        "bnd_up": (c4.shift(1) < bb_lo4.shift(1)) & (c4 >= bb_lo4),
        "bnd_dn": (c4.shift(1) > bb_hi4.shift(1)) & (c4 <= bb_hi4),
        "rcl_up": (c4.shift(1) < e20.shift(1)) & (c4 > e20),
        "rcl_dn": (c4.shift(1) > e20.shift(1)) & (c4 < e20),
        # event-quality depths (how stretched the market was before the turn)
        "osc_depth_l": ((p.osc_lo - rsi4.rolling(6).min().shift(1)) / p.osc_lo).clip(0, 1),
        "osc_depth_s": ((rsi4.rolling(6).max().shift(1) - p.osc_hi) / (100 - p.osc_hi)).clip(0, 1),
        "bnd_depth_l": ((bb_lo4 - d4["low"].rolling(3).min().shift(0)) / atr(d4, 14)).clip(0, 1),
        "bnd_depth_s": ((d4["high"].rolling(3).max().shift(0) - bb_hi4) / atr(d4, 14)).clip(0, 1),
        "rcl_depth_l": ((e20 - d4["low"].rolling(6).min().shift(1)) / atr(d4, 14)).clip(0, 1.5) / 1.5,
        "rcl_depth_s": ((d4["high"].rolling(6).max().shift(1) - e20) / atr(d4, 14)).clip(0, 1.5) / 1.5,
        # structural stop anchors from the 4H frame
        "low4": d4["low"], "high4": d4["high"],
        "swing3_lo4": d4["low"].rolling(3).min(),
        "swing3_hi4": d4["high"].rolling(3).max(),
    })
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


def hold_confidence(f: pd.DataFrame, state: pd.Series, btc_dir: pd.Series,
                    d: int, p: Params) -> pd.Series:
    """Continuous [0,1] conviction that an OPEN position in direction `d` still
    has its thesis intact THIS bar — the live re-score used by the hourly
    scoring loop and by BOTH engine fade exits: the winner-only SignalFade and
    the losing-side early cut (LossFade). It is direction-symmetric, so the same
    read protects a winner (bank a stalled/decayed run) and a loser (cut before
    the stop once regime/momentum have clearly turned adverse).

    Blends three a-priori (not fitted) reads of "is the trend/momentum that
    justified this trade still here?":
      - MTF alignment: 1D / 4H / BTC still pointing the trade's way,
      - regime fit: are we still in the trade's trend (vs decayed to RANGE / a
        new opposite state — the "새로운 상황으로 변경" case),
      - 4H momentum: price still on the right side of the EMA20 and RSI(4H)
        still leaning the trade's way (the "모멘텀을 잃음" case).
    """
    align = _tf_align(f, btc_dir, d)
    trend = "TREND_UP" if d == 1 else "TREND_DOWN"
    if p.hold_cont:
        # HV-A: continuous trend fit from the trade's own 4H frame. The stepped
        # daily-regime map (1.0/0.5/0.35) lags the tape by up to the hysteresis
        # window and makes hold_conf jump in 0.15 cliffs on each label flip; the
        # signed EMA20-EMA50 gap graded by ATR4H reads the same trend definition
        # regime.py uses (|EMA20-EMA50| > 0.5 ATR) as a smooth [0,1]. HIGH_VOL /
        # CRISIS are not trend states, so hard-zero fit there (unchanged intent).
        fit = ((d * (f["ema20_4h"] - f["ema50_4h"]) / f["atr4h"]).clip(-1, 1)
               * 0.5 + 0.5)
        fit = fit.where(~state.isin(["HIGH_VOL", "CRISIS"]), 0.0).fillna(0.0)
    else:
        fit = state.map({trend: 1.0, "RANGE": 0.5, "HIGH_VOL": 0.35}).fillna(0.0)
    mom = hold_momentum(f, d, p)
    return (0.45 * align + 0.30 * fit + 0.25 * mom).clip(0, 1)


def hold_momentum(f: pd.DataFrame, d: int, p: Params | None = None) -> pd.Series:
    """The 4H-momentum component of hold_confidence, exposed on its own: how
    decisively price sits on the trade's side of the value line (EMA20), graded
    by ATR4H instead of a binary sign — a hard 0/1 cliff makes hold_conf flicker
    whenever price hovers on the EMA and turns the streak-based fade exits
    jittery. Saturates at 1 ATR either way (0.5 == at the line), mirroring how
    rsi_ok saturates at 20 RSI points.

    mom == 0 is full ADVERSE saturation: price a whole ATR4H beyond the value
    line against the trade AND RSI(4H) on the wrong side of 50. The engine's
    LossFade uses that saturation as a second, price-based read of a broken
    thesis (E15) — the regime/alignment 75% of hold_confidence lags exactly
    when a V-reversal runs over a counter-trend position, so the blended score
    can stay above the loss floor while the chart has plainly turned.
    """
    px_ok = (d * (f["close"] - f["ema20_4h"]) / f["atr4h"]).clip(-1, 1) * 0.5 + 0.5
    if p is not None and p.hold_cont:
        # HV-A: symmetric rsi, restoring the adverse-side gradient. The old
        # clip(0,1) collapses the whole RSI4H<50 (for a long) region to 0, so
        # hold_conf could not tell RSI 45 from RSI 30 — exactly the region the
        # LossFade must grade. Now RSI a full 20 pts wrong side saturates to 0,
        # mirroring px_ok; mom==0 (the E15 "chart has plainly turned" second
        # read) therefore now means price 1 ATR adverse AND RSI 20 pts wrong.
        rsi_ok = (d * (f["rsi4h"] - 50) / 20).clip(-1, 1) * 0.5 + 0.5
    else:
        rsi_ok = (d * (f["rsi4h"] - 50) / 20).clip(0, 1)
    return 0.5 * px_ok + 0.5 * rsi_ok


def _playbook(name: str, d: int, f: pd.DataFrame, state: pd.Series,
              btc_dir: pd.Series, vol_ratio: pd.Series, p: Params):
    """Returns (mask, sl, base, fit, align) for one playbook slot."""
    body = f["body_up"] if d == 1 else f["body_dn"]
    wick_against = f["wick_lo"] if d == 1 else f["wick_hi"]
    family = name.split("_")[0]
    trend = "TREND_UP" if d == 1 else "TREND_DOWN"   # V3 direction-explicit state

    if family == "BRK":     # swing trend continuation
        level = f["hh"] if d == 1 else f["ll"]
        broke = (f["close"] > level) if d == 1 else (f["close"] < level)
        # E15: the global-BTC-direction hard gate was removed — with V3
        # per-asset regimes it double-counted BTC (still 1/3 of c_align) and
        # was the sole blocker on 23/94 recent live breakouts (measured FN).
        # The asset's own 1D bias / state / 4H bias gates stay as designed.
        mask = (broke & (f["bias1d"] == d) & (f["bias4h"] == d)
                & (vol_ratio >= p.brk_vol_mult)
                & state.isin([trend, "RANGE"]))
        sl = level - d * p.sl_swing_atr * f["atr1h"]
        margin = ((f["close"] - level) * d / f["atr1h"]).clip(0, 1)
        squeeze = (1 - f["bbw_pct"] / 100).clip(0, 1)
        base = (margin + squeeze + body.fillna(0) + (vol_ratio / 3).clip(0, 1)) / 4
        fit = state.map({trend: 1.0, "RANGE": 0.4}).fillna(0.0)
        align = _tf_align(f, btc_dir, d)

    elif family == "PBK":
        # V3 CONTINUOUS chart-state entry ("whale accumulation"): not a discrete
        # crossing — evaluated every bar. In a confirmed trend, whenever price
        # has pulled back into value (between the 4H EMA20 and EMA50, momentum
        # RESET but not broken) and a 1H bar closes back in the trend direction,
        # step in. Catches continuation entries that arm/trigger events miss.
        pull = (f["close"] - f["ema20_4h"]) / f["atr4h"] * d   # +above / -below EMA20
        deep = (f["close"] - f["ema50_4h"]) / f["atr4h"] * d   # distance past EMA50
        rsi_d = f["rsi1h"] if d == 1 else 100 - f["rsi1h"]
        reclaim = (f["close"] > f["prev_high"]) if d == 1 else (f["close"] < f["prev_low"])
        in_value = pull.between(-p.pbk_deep_atr, p.pbk_shallow_atr) & (deep > -p.pbk_deep_atr)
        mask = ((state == trend) & (f["bias1d"] == d) & (f["bias4h"] == d)
                & in_value & rsi_d.between(p.pbk_rsi_lo, p.pbk_rsi_hi)
                & reclaim & (body > 0.25) & (vol_ratio >= 1.0))
        swing = f["low"].rolling(8).min() if d == 1 else f["high"].rolling(8).max()
        sl = swing - d * p.sl_swing_atr * f["atr1h"]
        shallowness = (1 - (pull.clip(-p.pbk_deep_atr, 0).abs() / p.pbk_deep_atr)).clip(0, 1)
        base = (shallowness + body.fillna(0) + (vol_ratio / 3).clip(0, 1)) / 3
        fit = (state == trend).astype(float)
        align = _tf_align(f, btc_dir, d)

    elif family == "FADE":  # day-trade pullback fade at the 4H EMA20
        zone = (f["close"] - f["ema20_4h"]) / f["atr4h"] * d
        mask = ((f["bias1d"] == d) & (f["bias4h"] == d) & (state == trend)
                & zone.between(-p.fade_zone_atr, 0.2)
                & (body > 0) & (wick_against >= p.fade_wick)
                & (vol_ratio >= 1.0))
        ext = f["low"] if d == 1 else f["high"]
        sl = ext - d * p.sl_swing_atr * f["atr1h"]
        base = (wick_against + body.fillna(0) + (vol_ratio / 3).clip(0, 1)) / 3
        fit = (state == trend).astype(float)
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

    elif family in ("OSC", "BND", "RCL"):
        # V2 whale-scale pattern: a crossing EVENT on the closed 4H bar arms
        # the slot for the next 4 hours; a 1H confirmation bar in the trade
        # direction pulls the precision trigger. 1D bias is the referee.
        trig = ((f["close"] > f["prev_high"]) & (f["body_up"] > 0.3)) if d == 1 \
            else ((f["close"] < f["prev_low"]) & (f["body_dn"] > 0.3))
        if family == "OSC":     # RSI(14,4H) re-cross 30 up / 70 down
            armed = f["osc_up"] if d == 1 else f["osc_dn"]
            depth = f["osc_depth_l"] if d == 1 else f["osc_depth_s"]
            ext = f["low4"] if d == 1 else f["high4"]
            allowed = state.isin(["RANGE", "HIGH_VOL"])
            fit = pd.Series(0.6, index=f.index)
            fit[state == "RANGE"] = 1.0
        elif family == "BND":   # 2-sigma band re-entry on 4H closes
            armed = f["bnd_up"] if d == 1 else f["bnd_dn"]
            depth = f["bnd_depth_l"] if d == 1 else f["bnd_depth_s"]
            ext = f["low4"] if d == 1 else f["high4"]
            allowed = state == "RANGE"
            fit = (state == "RANGE").astype(float)
        else:                   # RCL: 4H EMA20 reclaim in the 1D trend
            armed = f["rcl_up"] if d == 1 else f["rcl_dn"]
            depth = f["rcl_depth_l"] if d == 1 else f["rcl_depth_s"]
            ext = f["swing3_lo4"] if d == 1 else f["swing3_hi4"]
            allowed = (state == trend) & (f["bias1d"] == d)
            fit = (state == trend).astype(float)
        # mean-reversion slots must not fight a confirmed 1D trend
        guard = (f["bias1d"] != -d) if family in ("OSC", "BND") else True
        mask = (armed.fillna(False).astype(bool) & trig & allowed & guard
                & (vol_ratio >= 1.0))
        sl = ext - d * 0.5 * f["atr4h"]
        base = (depth.fillna(0) + body.fillna(0) + (vol_ratio / 3).clip(0, 1)) / 3
        align = _tf_align(f, btc_dir, d) if family == "RCL" \
            else pd.Series(0.5, index=f.index)

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
        if p.conf_clean:
            # CV-A: the mask already hard-requires the regime/1D/4H conditions
            # that c_fit and c_align encode, so on the entry-conditional set
            # those components are saturated (c_align) or sign-inverted (c_fit,
            # RANGE-breakouts score highest R yet get fit=0.4). Drop them from
            # the SCORE — conf now grades only the continuous evidence (c_base).
            # Funding leaves the score too: instead of a bonus/penalty addend it
            # becomes a hard CROWDING VETO — a new entry crowded along its own
            # direction (fund_z*d > funding_z_ext) is blocked, never sized up.
            conf = base.clip(0, 1)
            crowded = (f["fund_z"].fillna(0.0) * d) > p.funding_z_ext
            mask = mask & ~crowded
        else:
            # B1 (V6): legacy composite score, but funding as a crowding VETO
            # instead of a score addend — byte-identical where funding history
            # is absent (design window), live-only tier-jump risk removed.
            fm_add = 0.0 if p.conf_fund_veto else fm
            conf = (p.w_base * base + p.w_regime * fit
                    + p.w_align * align + fm_add).clip(0, 1)
            if p.conf_fund_veto:
                crowded = (f["fund_z"].fillna(0.0) * d) > p.funding_z_ext
                mask = mask & ~crowded
        idx = f.index[mask.fillna(False)]
        if len(idx) == 0:
            continue
        # SEL-A (review): per-asset causal percentile of this slot's own base,
        # so the seat tiebreak compares slots on one scale (CV-B normalization,
        # reusing bbw_lookback — no new window). Inert unless seat_rank_cbase.
        base_pct = (pct_rank(base, p.bbw_lookback) / 100).clip(0, 1)
        rows.append(pd.DataFrame({
            "dir": d, "conf": conf.loc[idx], "sl": sl.loc[idx], "setup": name,
            "c_base": base.loc[idx], "c_fit": fit.loc[idx],
            "c_align": align.loc[idx], "c_fund": fm.loc[idx],
            "c_base_pct": base_pct.loc[idx]}, index=idx))
    if not rows:
        return pd.DataFrame(columns=CAND_COLS)
    # stable sort: on an exact conf tie the earlier playbook in config order
    # wins deterministically — the default quicksort is unstable, so a tie's
    # winner could flip with unrelated changes elsewhere in the array (a
    # live-vs-backtest reproducibility hazard, found in E20).
    cand = pd.concat(rows).sort_values("conf", ascending=False, kind="stable")
    cand = cand[~cand.index.duplicated(keep="first")].sort_index()
    # volatility floor: never place a stop inside sl_floor_atr * ATR of price
    close_c = f["close"].reindex(cand.index)
    floor = close_c - cand["dir"] * p.sl_floor_atr * f["atr1h"].reindex(cand.index)
    cand["sl"] = np.where(cand["dir"] == 1, np.minimum(cand["sl"], floor),
                          np.maximum(cand["sl"], floor))
    ok = (cand["sl"] - close_c) * cand["dir"] < 0
    return cand[ok & cand["conf"].ge(p.conf_entry)]
