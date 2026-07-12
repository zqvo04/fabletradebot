"""Incremental live replay (rolling anchor) — the resurrection fix.

The live loop replays only NEW bars each run, carrying the engine's internal
state forward. These tests pin the two properties that make that safe:
  1. a chunked replay (carry serialized through JSON, as run_live persists it)
     is byte-identical to a single full pass — persistence changed, not rules;
  2. starting from a later anchor WITHOUT carry does not re-derive an earlier
     position (a wiped/rolled state is a clean reset, never a resurrection).
"""
import numpy as np
import pandas as pd

from fabletradebot.config import Params
from fabletradebot.engine import deserialize_carry, run, serialize_carry


def _scenario(n=12):
    """Single BTC long fired at bar0 on a gently rising path (stays open)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    px = [(100, 100.6, 99.6, 100 + 0.2 * i) for i in range(n)]
    df = pd.DataFrame(px, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    f = pd.DataFrame(index=idx)
    f["atr1h"], f["bias4h"] = 1.0, 1.0
    cands = {"BTC": pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                 "setup": ["BRK_L"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    return idx, ({"BTC": df}, {"BTC": f}, cands, {"BTC": None}, regime, corr)


def test_chunked_replay_equals_single_pass():
    idx, args = _scenario()
    p = Params()
    full = run(*args, p, start=idx[0], equity0=10_000.0)

    mid = idx[5]
    c1 = run(*args, p, start=idx[0], end=mid, equity0=10_000.0)
    carry = deserialize_carry(serialize_carry(c1["carry"]))   # JSON round-trip
    c2 = run(*args, p, start=idx[6], equity0=10_000.0, carry=carry)

    assert abs(full["final_equity"] - c2["final_equity"]) < 1e-9
    assert sorted(full["open_positions"]) == sorted(c2["open_positions"])
    both = pd.concat([c1["trades"], c2["trades"]])
    assert len(both) == len(full["trades"])
    if len(full["trades"]):
        assert np.allclose(sorted(both["pnl"]), sorted(full["trades"]["pnl"]))


def test_rolling_anchor_does_not_resurrect_without_carry():
    idx, args = _scenario()
    p = Params()
    # position is open by mid-window
    c1 = run(*args, p, start=idx[0], end=idx[5], equity0=10_000.0)
    assert "BTC" in c1["open_positions"]

    # resume WITH carry -> the position is carried forward (correct live behavior)
    carry = deserialize_carry(serialize_carry(c1["carry"]))
    resumed = run(*args, p, start=idx[6], equity0=10_000.0, carry=carry)
    assert "BTC" in resumed["open_positions"]

    # start fresh from a LATER anchor with NO carry (a reset / wiped state) ->
    # the bar0 entry is NOT re-derived: history stays deleted, no resurrection
    fresh = run(*args, p, start=idx[6], equity0=10_000.0, carry=None)
    assert fresh["open_positions"] == {}
    assert len(fresh["trades"]) == 0


def test_carry_roundtrip_preserves_position_fields():
    idx, args = _scenario()
    res = run(*args, Params(), start=idx[0], end=idx[5], equity0=10_000.0)
    back = deserialize_carry(serialize_carry(res["carry"]))
    a, b = res["carry"], back
    assert a["cash"] == b["cash"] and a["peak"] == b["peak"]
    pa, pb = a["positions"]["BTC"], b["positions"]["BTC"]
    assert pa.entry == pb.entry and pa.sl == pb.sl and pa.tranches == pb.tranches
    assert pa.opened_ts == pb.opened_ts and pa.direction == pb.direction
