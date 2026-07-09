"""Data cleaning. Hard precondition from the blueprint: rows with N/A in
required fields are unconditionally dropped before anything is estimated."""
import pandas as pd

OHLCV = ["open", "high", "low", "close", "volume"]


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Drop N/A rows, non-positive prices, duplicate timestamps; sort by time."""
    df = df.copy()
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=OHLCV)
    df = df[(df[OHLCV[:4]] > 0).all(axis=1) & (df["volume"] >= 0)]
    return df


def resample_ohlcv(df: pd.DataFrame, freq: str = "4h") -> pd.DataFrame:
    """Aggregate 1H bars to a coarser frame; partial trailing bins dropped."""
    out = df.resample(freq).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=OHLCV[:4])


def align_funding(index: pd.DatetimeIndex, funding: pd.Series | None) -> pd.Series:
    """Align an 8h funding-rate series to the bar index.

    The prevailing funding rate is carried forward between funding events
    (this is the actual live rate, not imputation). Bars before the first
    funding observation stay NaN and evidence E3 degrades to neutral there.
    """
    if funding is None:
        return pd.Series(float("nan"), index=index)
    f = funding.dropna().sort_index()
    f = f[~f.index.duplicated(keep="last")]
    return f.reindex(index.union(f.index)).ffill().reindex(index)
