"""Regime-switching synthetic market generator for logic validation.

Produces correlated 1H OHLCV for BTC/ETH/SOL/HYPE plus crowding-driven 8h
funding, cycling through trend / chop / squeeze / spike segments so every
playbook and the regime engine get exercised."""
import numpy as np
import pandas as pd

SEGMENTS = (
    # (kind, drift per bar, vol multiplier, mean length in bars)
    ("trend_up", +0.0011, 1.0, 320),
    ("chop", 0.0, 0.9, 360),
    ("squeeze", 0.0, 0.35, 200),
    ("trend_down", -0.0011, 1.2, 280),
    ("chop", 0.0, 0.9, 360),
    ("spike", -0.004, 3.0, 40),
)


def _segment_path(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Per-bar (drift, vol_mult) arrays cycling through SEGMENTS with jitter."""
    drift = np.empty(n)
    volm = np.empty(n)
    i, k = 0, int(rng.integers(0, len(SEGMENTS)))
    while i < n:
        kind, mu, vm, mean_len = SEGMENTS[k % len(SEGMENTS)]
        length = max(20, int(rng.normal(mean_len, mean_len * 0.25)))
        j = min(i + length, n)
        drift[i:j] = mu * rng.normal(1.0, 0.2)
        volm[i:j] = vm
        i, k = j, k + 1
    return drift, volm


def _ohlc_from_returns(s0: float, ret: np.ndarray, vol: np.ndarray,
                       rng: np.random.Generator) -> pd.DataFrame:
    close = s0 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[s0], close[:-1]])
    wick_hi = np.abs(rng.normal(0.0, vol)) * close
    wick_lo = np.abs(rng.normal(0.0, vol)) * close
    high = np.maximum(open_, close) + wick_hi
    low = np.minimum(open_, close) - wick_lo
    base_vol = rng.lognormal(0.0, 0.4, len(ret))
    volume = base_vol * (1.0 + 8.0 * np.abs(ret) / (vol + 1e-9) * 0.2)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def generate_market(n_bars: int = 6000, seed: int = 7,
                    start: str = "2025-01-01") -> tuple[dict, dict]:
    """Returns (data, funding): per-asset OHLCV DataFrames and 8h funding Series."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n_bars, freq="1h", tz="UTC")

    btc_drift, btc_volm = _segment_path(n_bars, rng)
    base_vol = 0.005
    btc_vol = base_vol * btc_volm
    btc_ret = btc_drift + rng.normal(0.0, 1.0, n_bars) * btc_vol

    specs = {  # (s0, beta to BTC, idio vol mult, own-segment weight)
        "BTC": (60_000.0, 1.0, 0.0, 0.0),
        "ETH": (3_000.0, 1.1, 0.5, 0.3),
        "SOL": (150.0, 1.5, 0.9, 0.6),
        "HYPE": (25.0, 2.0, 1.6, 1.0),
    }
    data, funding = {}, {}
    for asset, (s0, beta, idio_mult, own_w) in specs.items():
        if asset == "BTC":
            ret, vol = btc_ret, btc_vol
        else:
            own_drift, own_volm = _segment_path(n_bars, rng)
            idio = rng.normal(0.0, 1.0, n_bars) * base_vol * idio_mult * own_volm
            ret = beta * btc_ret + own_w * own_drift + idio
            vol = np.sqrt((beta * btc_vol) ** 2 + (base_vol * idio_mult * own_volm) ** 2)
        df = _ohlc_from_returns(s0, ret, vol, rng)
        df.index = index
        data[asset] = df

        # crowding follows recent performance: funding ~ z of trailing 24h return
        c = df["close"]
        r24 = c.pct_change(24)
        z = (r24 - r24.rolling(240).mean()) / r24.rolling(240).std()
        f = (0.0001 * z.clip(-4, 4) + 0.00001).fillna(0.0)
        f8 = f[f.index.hour % 8 == 0]
        funding[asset] = f8.rename("funding")
    return data, funding
