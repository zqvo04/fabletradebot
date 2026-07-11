"""OKX public market data with CSV cache + incremental update.

Storage: {data_dir}/{SYM}_1H.csv  (ts_ms,open,high,low,close,volume)
         {data_dir}/{SYM}_funding.csv (ts_ms,rate)
All frames are indexed by UTC bar OPEN time; a 1H bar opening at t closes at
t+1h. Only confirmed (closed) candles are ever stored — that plus append-only
CSVs makes every run a deterministic replay.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

import pandas as pd

from .config import inst_id, spec

BASE = "https://www.okx.com"
_CANDLE_COLS = ["ts", "open", "high", "low", "close", "volume"]


def _get(path: str, params: dict, retries: int = 4) -> list:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fabletradebot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read())
            if payload.get("code") != "0":
                raise RuntimeError(f"OKX {path} error: {payload.get('code')} {payload.get('msg')}")
            return payload["data"]
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)
    return []


def _rows_to_df(rows: list) -> pd.DataFrame:
    """OKX candle rows (newest first, confirm flag last) -> confirmed-only df."""
    recs = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows if r[8] == "1"]
    df = pd.DataFrame(recs, columns=_CANDLE_COLS)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def fetch_candles(symbol: str, since_ms: int, until_ms: int | None = None) -> pd.DataFrame:
    """Fetch confirmed 1H candles in [since_ms, until_ms] via paged history walk."""
    inst = inst_id(symbol)
    frames, cursor = [], until_ms
    while True:
        params = {"instId": inst, "bar": "1H", "limit": "100"}
        if cursor is not None:
            params["after"] = str(cursor)  # returns records strictly older than cursor
        rows = _get("/api/v5/market/history-candles", params)
        if not rows:
            break
        df = _rows_to_df(rows)
        frames.append(df)
        oldest = int(rows[-1][0])
        if oldest <= since_ms or len(rows) < 100:
            break
        cursor = oldest
        time.sleep(0.12)
    # top up the freshest bars (history endpoint can lag a little)
    rows = _get("/api/v5/market/candles", {"instId": inst, "bar": "1H", "limit": "300"})
    if rows:
        frames.append(_rows_to_df(rows))
    if not frames:
        return pd.DataFrame(columns=_CANDLE_COLS)
    out = pd.concat(frames).drop_duplicates("ts").sort_values("ts")
    out = out[out.ts >= since_ms]
    if until_ms is not None:
        out = out[out.ts <= until_ms]
    return out.reset_index(drop=True)


def fetch_funding(symbol: str, since_ms: int) -> pd.DataFrame:
    inst = inst_id(symbol)
    frames, cursor = [], None
    while True:
        params = {"instId": inst, "limit": "100"}
        if cursor is not None:
            params["after"] = str(cursor)
        rows = _get("/api/v5/public/funding-rate-history", params)
        if not rows:
            break
        df = pd.DataFrame([(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows],
                          columns=["ts", "rate"])
        frames.append(df)
        oldest = int(rows[-1]["fundingTime"])
        if oldest <= since_ms or len(rows) < 100:
            break
        cursor = oldest
        time.sleep(0.12)
    if not frames:
        return pd.DataFrame(columns=["ts", "rate"])
    out = pd.concat(frames).drop_duplicates("ts").sort_values("ts")
    return out[out.ts >= since_ms].reset_index(drop=True)


def _path(data_dir: str, symbol: str, kind: str) -> str:
    return os.path.join(data_dir, f"{symbol}_{kind}.csv")


def update_cache(symbol: str, data_dir: str) -> tuple[int, int]:
    """Incrementally extend the CSV cache. Returns (#new candles, #new fundings)."""
    os.makedirs(data_dir, exist_ok=True)
    listed_ms = int(pd.Timestamp(spec(symbol).listed, tz="UTC").timestamp() * 1000)

    cpath = _path(data_dir, symbol, "1H")
    old = pd.read_csv(cpath) if os.path.exists(cpath) else pd.DataFrame(columns=_CANDLE_COLS)
    since = int(old.ts.max()) + 1 if len(old) else listed_ms
    fresh = fetch_candles(symbol, since_ms=since)
    candles = pd.concat([old, fresh]).drop_duplicates("ts").sort_values("ts")
    candles.to_csv(cpath, index=False)

    fpath = _path(data_dir, symbol, "funding")
    oldf = pd.read_csv(fpath) if os.path.exists(fpath) else pd.DataFrame(columns=["ts", "rate"])
    fsince = int(oldf.ts.max()) + 1 if len(oldf) else listed_ms
    freshf = fetch_funding(symbol, since_ms=fsince)
    funding = pd.concat([oldf, freshf]).drop_duplicates("ts").sort_values("ts")
    funding.to_csv(fpath, index=False)
    return len(fresh), len(freshf)


def load_1h(symbol: str, data_dir: str) -> pd.DataFrame:
    df = pd.read_csv(_path(data_dir, symbol, "1H"))
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    df.index.name = "open_time"
    return df


def load_funding(symbol: str, data_dir: str) -> pd.Series:
    df = pd.read_csv(_path(data_dir, symbol, "funding"))
    idx = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    return pd.Series(df["rate"].values, index=idx, name="rate")


def resample(df_1h: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Resample 1H OHLCV (open-time indexed) to N-hour bars anchored at 00:00 UTC.
    Only bars whose full window is covered by 1H data are kept (no partials)."""
    rule = f"{hours}h"
    agg = df_1h.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = df_1h["close"].resample(rule, label="left", closed="left").count()
    return agg[counts == hours].dropna()


def closed_asof_1h(feat: pd.DataFrame | pd.Series, bar_hours: int,
                   hourly_index: pd.DatetimeIndex):
    """Project features computed on N-hour bars onto the 1H grid without lookahead.

    Row t of the result (t = 1H bar open time) holds the value of the latest
    N-hour bar that had CLOSED by the decision time t+1h (i.e. when the 1H bar
    that opened at t has itself closed).
    """
    shifted = feat.copy()
    shifted.index = shifted.index + pd.Timedelta(hours=bar_hours)  # close times
    out = shifted.reindex(shifted.index.union(hourly_index + pd.Timedelta(hours=1))) \
                 .ffill().reindex(hourly_index + pd.Timedelta(hours=1))
    out.index = hourly_index
    return out
