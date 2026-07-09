"""OKX public-API data fetcher (no auth required) with CSV caching.

Endpoints:
  GET /api/v5/market/history-candles      (100 bars/page, paginate by `after`)
  GET /api/v5/public/funding-rate-history (100 rows/page, paginate by `after`)
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://www.okx.com"
INSTRUMENTS = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
    "SOL": "SOL-USDT-SWAP",
    "HYPE": "HYPE-USDT-SWAP",
}


def _get(path: str, params: dict, retries: int = 5) -> list:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (fabletradebot)"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            if payload.get("code") != "0":
                raise RuntimeError(f"OKX error {payload.get('code')}: {payload.get('msg')}")
            return payload["data"]
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return []


def fetch_candles(inst_id: str, start: pd.Timestamp, end: pd.Timestamp,
                  bar: str = "1H", pause: float = 0.12) -> pd.DataFrame:
    """1H OHLCV between start/end (UTC), oldest->newest. Only confirmed bars."""
    start_ms = int(start.timestamp() * 1000)
    after = int(end.timestamp() * 1000) + 3_600_000  # exclusive upper cursor
    rows = []
    while True:
        data = _get("/api/v5/market/history-candles",
                    {"instId": inst_id, "bar": bar, "limit": "100", "after": str(after)})
        if not data:
            break
        rows.extend(data)
        oldest = int(data[-1][0])
        if oldest <= start_ms or len(data) < 100:
            break
        after = oldest
        time.sleep(pause)  # stay under 20 req/2s
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volQuote", "confirm"])
    df = df[df["confirm"] == "1"]  # drop the still-forming bar
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    out = df[["open", "high", "low", "close", "volCcy"]].astype(float)
    out.columns = ["open", "high", "low", "close", "volume"]  # volume in base ccy
    return out.loc[(out.index >= start) & (out.index <= end)]


def fetch_funding(inst_id: str, start: pd.Timestamp, end: pd.Timestamp,
                  pause: float = 0.25) -> pd.Series:
    """Realized 8h funding rates between start/end (UTC), oldest->newest."""
    start_ms = int(start.timestamp() * 1000)
    after = int(end.timestamp() * 1000) + 1
    rows = []
    while True:
        data = _get("/api/v5/public/funding-rate-history",
                    {"instId": inst_id, "limit": "100", "after": str(after)})
        if not data:
            break
        rows.extend(data)
        oldest = int(data[-1]["fundingTime"])
        if oldest <= start_ms or len(data) < 100:
            break
        after = oldest
        time.sleep(pause)
    if not rows:
        return pd.Series(dtype=float, name="funding")
    s = pd.Series(
        {pd.to_datetime(int(r["fundingTime"]), unit="ms", utc=True): float(r["fundingRate"])
         for r in rows}, name="funding").sort_index()
    return s.loc[(s.index >= start) & (s.index <= end)]


def load_market(start: str, end: str, cache_dir: str = "data",
                assets: dict | None = None) -> tuple[dict, dict]:
    """Fetch-or-load-from-cache OHLCV + funding for all assets.
    Returns ({asset: DataFrame}, {asset: Series})."""
    assets = assets or INSTRUMENTS
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    cache = Path(cache_dir)
    cache.mkdir(exist_ok=True)
    data, funding = {}, {}
    for asset, inst in assets.items():
        c_path = cache / f"{asset}_1H.csv"
        f_path = cache / f"{asset}_funding.csv"
        if c_path.exists():
            df = pd.read_csv(c_path, index_col=0, parse_dates=True)
        else:
            df = fetch_candles(inst, start_ts, end_ts)
            if not df.empty:
                df.to_csv(c_path)
        if f_path.exists():
            fs = pd.read_csv(f_path, index_col=0, parse_dates=True).iloc[:, 0]
            fs.name = "funding"
        else:
            fs = fetch_funding(inst, start_ts, end_ts)
            if not fs.empty:
                fs.to_frame().to_csv(f_path)
        if df.empty:
            continue  # asset not available in the window (e.g. pre-listing)
        data[asset] = df
        funding[asset] = fs
    return data, funding


def update_market(start: str, cache_dir: str = "live_data",
                  assets: dict | None = None) -> tuple[dict, dict]:
    """Incremental refresh for the live loop: extend cached CSVs from their
    last timestamp to now, so an hourly run costs ~1 request per asset."""
    assets = assets or INSTRUMENTS
    start_ts = pd.Timestamp(start, tz="UTC")
    now = pd.Timestamp.now(tz="UTC").floor("h")
    cache = Path(cache_dir)
    cache.mkdir(exist_ok=True)
    data, funding = {}, {}
    for asset, inst in assets.items():
        c_path = cache / f"{asset}_1H.csv"
        f_path = cache / f"{asset}_funding.csv"
        old = pd.read_csv(c_path, index_col=0, parse_dates=True) if c_path.exists() else None
        fetch_from = old.index.max() + pd.Timedelta(hours=1) if old is not None else start_ts
        fresh = fetch_candles(inst, fetch_from, now) if fetch_from <= now else pd.DataFrame()
        df = pd.concat([d for d in (old, fresh) if d is not None and not d.empty])
        if df.empty:
            continue
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_csv(c_path)

        old_f = (pd.read_csv(f_path, index_col=0, parse_dates=True).iloc[:, 0]
                 if f_path.exists() else None)
        f_from = old_f.index.max() + pd.Timedelta(hours=1) if old_f is not None else start_ts
        fresh_f = fetch_funding(inst, f_from, now) if f_from <= now else pd.Series(dtype=float)
        fs = pd.concat([s for s in (old_f, fresh_f) if s is not None and not s.empty])
        if not fs.empty:
            fs = fs[~fs.index.duplicated(keep="last")].sort_index()
            fs.rename("funding").to_frame().to_csv(f_path)
        data[asset], funding[asset] = df, (fs if not fs.empty else None)
    return data, funding
