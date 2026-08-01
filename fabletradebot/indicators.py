"""Pure indicator functions. Row t of every output uses bars <= t only."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def bollinger_width(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.Series:
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    return (2 * k * sd) / mid


def pct_rank(s: pd.Series, window: int) -> pd.Series:
    """Percentile (0-100) of the current value within its trailing window."""
    return s.rolling(window).rank(pct=True) * 100


def donchian(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
    """Prior-bar channel: highest high / lowest low over the period ENDING at t-1,
    so a breakout of bar t is measured against levels known before the bar."""
    hi = df["high"].rolling(period).max().shift(1)
    lo = df["low"].rolling(period).min().shift(1)
    return hi, lo


def realized_vol(close: pd.Series, window: int) -> pd.Series:
    return close.pct_change().rolling(window).std(ddof=0)


def zscore(s: pd.Series, window: int) -> pd.Series:
    m = s.rolling(window, min_periods=window // 3).mean()
    sd = s.rolling(window, min_periods=window // 3).std(ddof=0)
    return (s - m) / sd.replace(0, np.nan)
