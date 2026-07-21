"""X-R trend-staleness demotion (regime.py): a downtrend that stops printing
new 20D closing lows demotes to RANGE after trend_stale_days, while a trend
still making new lows is never touched (runner safety is structural)."""
from dataclasses import replace

import numpy as np
import pandas as pd

from fabletradebot.config import Params
from fabletradebot.regime import raw_regime_1d

def _frame():
    """150 daily bars declining 1%/day (a new 20D close low every bar), then a
    30-day box strictly above the final decline low (no new lows print). The
    box range is tiny so ATR keeps shrinking with the EMA gap and the strength
    ratio stays >0.5 — the un-demoted state remains TREND_DOWN (the measured
    AVAX pathology)."""
    idx = pd.date_range("2024-01-01", periods=180, freq="D", tz="UTC")
    close = np.empty(180)
    close[:150] = 100.0 * 0.99 ** np.arange(150)
    level = close[149]
    close[150:] = level * (1.001 + 0.0005 * (np.arange(30) % 2))
    df = pd.DataFrame({"close": close}, index=idx)
    df["open"] = df["close"].shift(1).fillna(close[0])
    df["high"] = df["close"] * 1.005
    df["low"] = df["close"] * 0.995
    df["volume"] = 1.0
    return df

def test_stale_downtrend_demotes_to_range_but_fresh_trend_untouched():
    d1 = _frame()
    off = raw_regime_1d(d1, Params())["raw_state"]
    on = raw_regime_1d(d1, replace(Params(), trend_stale_days=20))["raw_state"]
    # while new 20D lows are printing (decline tail), the flag changes nothing
    assert (off.iloc[120:150] == on.iloc[120:150]).all()
    # deep in the box (age > 20 without a new low): still TREND_DOWN without
    # the flag (gap and ATR shrank together), demoted to RANGE with it
    assert off.iloc[175] == "TREND_DOWN"
    assert on.iloc[175] == "RANGE"

def test_stale_uptrend_is_not_demoted():
    # mirror frame: rally then box — DOWN-only staleness must leave TREND_UP
    d1 = _frame()
    for col in ("open", "high", "low", "close"):
        d1[col] = 200.0 - d1[col]          # invert: decline -> rally
    d1["high"], d1["low"] = 200.0 - _frame()["low"], 200.0 - _frame()["high"]
    off = raw_regime_1d(d1, Params())["raw_state"]
    on = raw_regime_1d(d1, replace(Params(), trend_stale_days=20))["raw_state"]
    assert (off == on).all()
