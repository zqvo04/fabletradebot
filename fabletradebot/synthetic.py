"""Synthetic OHLCV generators for mechanism tests (G1). Seeded, deterministic."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_1h(n_bars: int = 4000, seed: int = 7, drift: float = 0.0,
            vol: float = 0.01, start: str = "2024-01-01",
            regime_switch: bool = False) -> pd.DataFrame:
    """Random-walk 1H OHLCV. With regime_switch, alternates trend/chop blocks."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_bars, freq="1h", tz="UTC")
    mu = np.full(n_bars, drift)
    if regime_switch:
        block = 500
        for k in range(0, n_bars, block):
            mu[k:k + block] = drift + (0.0008 if (k // block) % 2 == 0 else -0.0002)
    rets = rng.normal(mu, vol)
    close = 100 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[100.0], close[:-1]])
    spread = np.abs(rng.normal(0, vol, n_bars)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.lognormal(10, 0.5, n_bars)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


def make_funding(index_1h: pd.DatetimeIndex, seed: int = 7) -> pd.Series:
    rng = np.random.default_rng(seed)
    times = pd.date_range(index_1h[0].ceil("8h"), index_1h[-1], freq="8h", tz="UTC")
    return pd.Series(rng.normal(0.0001, 0.0002, len(times)), index=times, name="rate")
