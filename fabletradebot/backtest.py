"""Orchestration: load data -> features -> regime -> candidates -> engine run,
plus summary metrics. Used by run_backtest.py, validation.py and run_live.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import UNIVERSE, Params
from .data_okx import load_1h, load_funding, resample
from .engine import run as engine_run
from .regime import corr_alert_1h, regime_1d
from .signals import build_features, hold_confidence, hold_momentum, scan


def load_universe(data_dir: str, symbols: list[str] | None = None):
    symbols = symbols or list(UNIVERSE)
    frames, funding = {}, {}
    for s in symbols:
        try:
            df = load_1h(s, data_dir)
        except FileNotFoundError:
            continue
        if len(df) < 300:
            continue
        frames[s] = df
        try:
            funding[s] = load_funding(s, data_dir)
        except FileNotFoundError:
            funding[s] = None
    return frames, funding


def prepare(frames: dict, funding: dict, p: Params):
    """Features / regime / candidates for the whole data span (pure, deterministic).

    V3: regime is classified PER ASSET on its own daily bars, then BTC CRISIS
    is overridden in as a systemic gate. The scanner sees the asset's own state
    (so an alt can trend while BTC ranges) plus the GLOBAL BTC direction for
    trend-alignment filters. Returns `states` (dict sym -> 1H state Series) for
    the engine and per-asset regime frames for the scanner.
    """
    from .data_okx import closed_asof_1h
    from .regime import effective_state

    grid = pd.DatetimeIndex(sorted(set().union(*[df.index for df in frames.values()])))
    btc_reg = regime_1d(resample(frames["BTC"], 24), p)
    btc_reg_h = closed_asof_1h(btc_reg, 24, grid)
    btc_state = btc_reg_h["state"].fillna("RANGE")
    btc_dir = btc_reg_h["btc_dir"].fillna(0)

    closes = pd.DataFrame({s: frames[s]["close"] for s in frames}).reindex(grid)
    corr = corr_alert_1h(closes, p)

    features, candidates, states = {}, {}, {}
    for s, df in frames.items():
        a_reg = regime_1d(resample(df, 24), p)
        a_state = closed_asof_1h(a_reg[["state"]], 24, df.index)["state"].fillna("RANGE")
        eff = effective_state(a_state, btc_state)   # BTC crisis override
        states[s] = eff
        # scanner regime frame: per-asset state + GLOBAL btc direction
        reg_s = pd.DataFrame({"state": eff, "btc_dir": btc_dir.reindex(df.index).ffill().fillna(0)})
        f = build_features(df, funding.get(s), p)
        # live re-score of a would-be OPEN position in either direction — the
        # engine reads the held side each bar for the momentum-fade exit
        f["hold_L"] = hold_confidence(f, reg_s["state"], reg_s["btc_dir"], 1, p)
        f["hold_S"] = hold_confidence(f, reg_s["state"], reg_s["btc_dir"], -1, p)
        f["mom_L"] = hold_momentum(f, 1)
        f["mom_S"] = hold_momentum(f, -1)
        features[s] = f
        candidates[s] = scan(f, reg_s, p)
    return features, candidates, states, corr


def run_backtest(data_dir: str, p: Params | None = None, start=None, end=None,
                 equity0: float = 10_000.0, symbols: list[str] | None = None) -> dict:
    p = p or Params()
    frames, funding = load_universe(data_dir, symbols)
    features, candidates, states, corr = prepare(frames, funding, p)
    start = pd.Timestamp(start, tz="UTC") if start else None
    end = pd.Timestamp(end, tz="UTC") if end else None
    res = engine_run(frames, features, candidates, funding, states, corr, p,
                     start=start, end=end, equity0=equity0)
    res["metrics"] = metrics(res["trades"], res["equity"], equity0)
    return res


def metrics(trades: pd.DataFrame, equity: pd.Series, equity0: float) -> dict:
    if len(trades) == 0:
        return {"trades": 0}
    r = trades["r"]
    wins = trades[trades.pnl > 0]
    losses = trades[trades.pnl <= 0]
    dd = (equity / equity.cummax() - 1).min() if len(equity) else 0.0
    monthly = equity.resample("ME").last().pct_change().dropna() if len(equity) else pd.Series()
    sharpe_m = monthly.mean() / monthly.std(ddof=0) * np.sqrt(12) \
        if len(monthly) > 1 and monthly.std(ddof=0) > 0 else np.nan
    total_ret = equity.iloc[-1] / equity0 - 1 if len(equity) else 0.0
    months = max(len(monthly), 1)
    return {
        "trades": int(len(trades)),
        "win_rate": round(len(wins) / len(trades), 4),
        "avg_r": round(r.mean(), 4),
        "expectancy_r": round(r.mean(), 4),
        "median_r": round(r.median(), 4),
        "profit_factor": round(wins.pnl.sum() / max(1e-9, -losses.pnl.sum()), 3),
        "total_return": round(total_ret, 4),
        "monthly_geo": round((1 + total_ret) ** (1 / months) - 1, 4),
        "max_dd": round(float(dd), 4),
        "sharpe_monthly": round(float(sharpe_m), 3) if sharpe_m == sharpe_m else None,
        "avg_bars": round(float(trades["bars"].mean()), 1),
        "avg_leverage": round(float(trades["leverage"].mean()), 2),
    }


def breakdown(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    if len(trades) == 0:
        return pd.DataFrame()
    g = trades.groupby(by)
    return pd.DataFrame({
        "n": g.size(), "win_rate": g.apply(lambda x: (x.pnl > 0).mean(), include_groups=False),
        "avg_r": g["r"].mean(), "sum_pnl": g["pnl"].sum(),
    }).round(4)
