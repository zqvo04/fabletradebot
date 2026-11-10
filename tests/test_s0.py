"""S0 stop/MAE instrumentation (STOP_REDESIGN T3): attribution-only fields."""
import csv
import json

import pandas as pd

from fabletradebot import promotion, shadow
from fabletradebot.config import Params
from fabletradebot.engine import _pos_from_dict, _pos_to_dict, deserialize_carry, run, serialize_carry

NEW = ["sl0", "init_stop_frac", "stop_atr", "mae_r", "bars_to_peak", "bias4h_entry"]

# up to 103, dip to 99, then collapse through the 95 stop
PATH = [100, 100, 101, 103, 101, 99, 99, 90, 90, 90]


def _scenario(closes, bias=1.0):
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": closes, "close": closes}, index=idx, dtype=float)
    df["high"] = df[["open", "close"]].max(axis=1) + 0.1
    df["low"] = df[["open", "close"]].min(axis=1) - 0.1
    df["volume"] = 1000.0
    f = pd.DataFrame(index=idx)
    f["atr1h"], f["bias4h"] = 2.0, bias
    cands = {"BTC": pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                 "setup": ["BRK_L"]}, index=[idx[0]])}
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    return idx, ({"BTC": df}, {"BTC": f}, cands, {"BTC": None}, regime,
                 pd.Series(False, index=idx))


def test_trade_record_has_s0_fields():
    _, args = _scenario(PATH)
    t = run(*args, Params(), equity0=10_000.0)["trades"]
    assert len(t) == 1
    r = t.iloc[0]
    assert r["reason"] == "SL" and r["sl0"] == 95.0
    assert r["init_stop_frac"] > 0
    assert abs(r["stop_atr"] - 5.0 / 2.0) < 0.1     # |entry-sl0| / ATR, minus slippage
    assert r["mae_r"] < 0 and r["peak_r"] > 0
    assert 1 <= r["bars_to_peak"] <= r["bars"]
    assert r["bias4h_entry"] == 1


def test_bias_entry_is_direction_signed():
    _, args = _scenario(PATH, bias=-1.0)
    assert run(*args, Params(), equity0=10_000.0)["trades"].iloc[0]["bias4h_entry"] == -1   # bias -1 x dir +1


def test_carry_roundtrip_and_old_carry_back_compat():
    idx, args = _scenario(PATH)
    c = run(*args, Params(), end=idx[4], equity0=10_000.0)["carry"]
    live = c["positions"]["BTC"]
    pos = deserialize_carry(json.loads(json.dumps(serialize_carry(c))))["positions"]["BTC"]
    assert pos.mae_r == live.mae_r
    assert pos.bars_to_peak == live.bars_to_peak > 0
    # OLD carry: new Position fields and meta stamps absent -> defaults
    old = _pos_to_dict(pos)
    del old["mae_r"], old["bars_to_peak"]
    old["meta"] = {k: v for k, v in old["meta"].items()
                   if k not in ("atr_entry", "bias4h_entry")}
    p2 = _pos_from_dict(old)
    assert (p2.mae_r, p2.bars_to_peak) == (0.0, 0)


def _append(mod, rec, path):
    if mod is shadow:
        shadow.append_trade(rec, "Live-Taken", path)
    else:
        promotion.append_trade(rec, path)


def test_ledger_migration_is_lossless(tmp_path):
    for mod, old_n in ((shadow, 15), (promotion, 10)):
        path = str(tmp_path / f"{old_n}.csv")
        cols = mod.LEDGER_COLS
        old_cols = cols[:old_n]
        with open(path, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(old_cols)
            for i in range(3):
                w.writerow([f"x{i}"] + ["0.1234567890123456789"] * (old_n - 1))
        before = pd.read_csv(path, dtype=str)
        rec = {k: 1.5 for k in cols}
        _append(mod, rec, path)
        _append(mod, rec, path)          # second append: no further migration
        after = pd.read_csv(path, dtype=str)
        assert list(after.columns) == cols and len(after) == 5
        pd.testing.assert_frame_equal(after.iloc[:3][old_cols].reset_index(drop=True), before)
        assert after.iloc[:3][NEW].isna().all().all()
        assert after.iloc[3:][NEW].notna().all().all()


def test_fresh_ledger_has_new_header(tmp_path):
    path = str(tmp_path / "n.csv")
    promotion.append_trade({"closed": "t"}, path)
    promotion.append_trade({"closed": "u"}, path)
    df = pd.read_csv(path)
    assert list(df.columns) == promotion.LEDGER_COLS and len(df) == 2
