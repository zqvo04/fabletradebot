"""Market Regime Engine: feature computation + regime classification.

Regimes: TREND_UP, TREND_DOWN, SQUEEZE, CHOP, CRISIS, WARMUP.
Switches require `hysteresis` consecutive confirming bars — except a switch
INTO CRISIS, which applies immediately (defense without delay).
"""
import numpy as np
import pandas as pd

from .config import Config
from . import indicators as ind
from .preprocess import align_funding

TREND_UP = "TREND_UP"
TREND_DOWN = "TREND_DOWN"
SQUEEZE = "SQUEEZE"
CHOP = "CHOP"
CRISIS = "CRISIS"
WARMUP = "WARMUP"

CORE_FEATURES = ["atr", "er", "tstat", "sigma", "v_pct", "bbw_pct", "don_hi", "don_lo"]


def build_features(df: pd.DataFrame, funding: pd.Series | None, cfg: Config) -> pd.DataFrame:
    """Compute all features + the confirmed regime column for one asset.
    `df` must already be cleaned (see preprocess.clean_ohlcv)."""
    out = df[["open", "high", "low", "close", "volume"]].copy()
    c = out["close"]

    ret = np.log(c / c.shift(1))
    ret_w = ind.winsorize_returns(ret, cfg.winsor_sigma)  # estimators only
    out["ret"] = ret
    out["sigma"] = ind.ewma_vol(ret_w, cfg.ewma_lam)
    out["ret_sigma"] = ret / out["sigma"].shift(1)  # raw shock vs prior vol

    out["atr"] = ind.atr(out, cfg.atr_n)
    out["ema20"] = c.ewm(span=20, adjust=False).mean()
    out["ema100"] = c.ewm(span=100, adjust=False).mean()
    out["er"] = ind.efficiency_ratio(c, cfg.er_n)
    out["tstat"] = ind.ols_tstat(np.log(c), cfg.tstat_n)
    out["v_pct"] = ind.pct_rank(out["sigma"], cfg.vpct_win)

    out["bbw"] = ind.bollinger_width(c, cfg.bbw_n, cfg.bbw_k)
    out["bbw_pct"] = ind.pct_rank(out["bbw"], cfg.bbwpct_win)
    volvol = out["sigma"].diff().rolling(cfg.volvol_win).std()
    out["volvol_pct"] = ind.pct_rank(volvol, cfg.volvolpct_win)

    out["don_hi"], out["don_lo"] = ind.donchian(out, cfg.don_slow)
    out["don_hi_f"], out["don_lo_f"] = ind.donchian(out, cfg.don_fast)
    ex = cfg.swing_exclude
    out["swing_hi"] = out["high"].rolling(cfg.swing_win).max().shift(1 + ex)
    out["swing_lo"] = out["low"].rolling(cfg.swing_win).min().shift(1 + ex)

    out["vol_sma"] = out["volume"].rolling(cfg.vol_sma).mean()
    rng = (out["high"] - out["low"]).replace(0.0, np.nan)
    clv = ((c - out["low"]) - (out["high"] - c)) / rng  # candle location in [-1, 1]
    vol_ratio = (out["volume"] / out["vol_sma"]).clip(upper=2.0)
    out["e2raw"] = (clv * vol_ratio / 2.0).fillna(0.0)  # taker-delta proxy in [-1, 1]

    out["ret4h"] = c.pct_change(4)

    out["funding"] = align_funding(out.index, funding)
    if funding is not None:
        fr = funding.dropna().sort_index()
        fr = fr[~fr.index.duplicated(keep="last")]
        fz = (fr - fr.rolling(cfg.funding_z_win).mean()) / fr.rolling(cfg.funding_z_win).std()
        out["f_z"] = align_funding(out.index, fz)
    else:
        out["f_z"] = np.nan

    out["regime"] = _classify(out, cfg)
    return out


def _raw_regime(row, cfg: Config) -> str:
    if any(np.isnan(row[k]) for k in CORE_FEATURES):
        return WARMUP
    if row["v_pct"] >= cfg.vpct_crisis and (
        row["ret_sigma"] <= cfg.ret_sigma_crisis or row["volvol_pct"] >= cfg.volvol_crisis
    ):
        return CRISIS
    if row["er"] >= cfg.er_trend and abs(row["tstat"]) >= cfg.tstat_min:
        return TREND_UP if row["tstat"] > 0 else TREND_DOWN
    if row["bbw_pct"] <= cfg.bbw_squeeze:
        return SQUEEZE
    return CHOP


def _classify(feats: pd.DataFrame, cfg: Config) -> list[str]:
    cols = CORE_FEATURES + ["ret_sigma", "volvol_pct"]
    arr = {k: feats[k].to_numpy() for k in cols}
    n = len(feats)
    confirmed, pending, streak = WARMUP, None, 0
    out = []
    for i in range(n):
        row = {k: arr[k][i] for k in cols}
        raw = _raw_regime(row, cfg)
        if raw == confirmed:
            pending, streak = None, 0
        elif raw == CRISIS or confirmed == WARMUP:
            confirmed, pending, streak = raw, None, 0  # immediate
        elif raw == pending:
            streak += 1
            if streak >= cfg.hysteresis:
                confirmed, pending, streak = raw, None, 0
        else:
            pending, streak = raw, 1
        out.append(confirmed)
    return out
