"""Pure indicator functions (vectorized where practical)."""
import numpy as np
import pandas as pd


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    """Kaufman ER: |net move| / sum of absolute moves, in [0, 1]."""
    change = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n).sum()
    return (change / path).where(path > 0, 0.0)


def _tstat_window(y: np.ndarray) -> float:
    n = len(y)
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    b = ((x - xm) * (y - ym)).sum() / sxx
    resid = y - (ym - b * xm) - b * x
    sse = (resid**2).sum()
    if sse <= 1e-18:
        return np.sign(b) * 50.0
    se = np.sqrt(sse / (n - 2) / sxx)
    return b / se


def ols_tstat(logp: pd.Series, n: int) -> pd.Series:
    """t-statistic of the OLS slope of log-price over a rolling window."""
    return logp.rolling(n).apply(_tstat_window, raw=True)


def ewma_vol(ret: pd.Series, lam: float) -> pd.Series:
    """Per-bar EWMA volatility: sigma^2_t = lam*sigma^2_{t-1} + (1-lam)*r^2_t."""
    return ret.pow(2).ewm(alpha=1 - lam, adjust=False).mean().pow(0.5)


def pct_rank(s: pd.Series, window: int) -> pd.Series:
    """Rolling percentile rank (0-100) of the latest value."""
    return s.rolling(window, min_periods=window).rank(pct=True) * 100.0


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def bollinger_width(close: pd.Series, n: int, k: float) -> pd.Series:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std()
    return (2 * k * sd / mid).where(mid > 0)


def donchian(df: pd.DataFrame, n: int) -> tuple[pd.Series, pd.Series]:
    """(high, low) of the channel over the PRIOR n bars (excludes current bar)."""
    return (
        df["high"].rolling(n).max().shift(1),
        df["low"].rolling(n).min().shift(1),
    )


def winsorize_returns(ret: pd.Series, n_sigma: float) -> pd.Series:
    """Clip returns at +-n_sigma of trailing vol — for estimators only.
    Raw returns are kept elsewhere so genuine crash bars still trigger CRISIS."""
    ref = ret.rolling(100, min_periods=30).std().shift(1)
    lo, hi = -n_sigma * ref, n_sigma * ref
    return ret.clip(lower=lo, upper=hi).where(ref.notna(), ret)
