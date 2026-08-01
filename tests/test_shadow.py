"""Shadow book (learning-only replay) — the properties that make it safe.

The shadow book exists to multiply the forward record; it earns that only if it
provably cannot touch the live one. These tests pin the isolation:
  1. the size_equity field is inert at its default (live/backtest byte-identical);
  2. lifting the seat produces MORE trades on bars where the seat was the binding
     constraint, and each of them is managed by the same exit legs;
  3. shadow sizing is decoupled — R does not depend on the shadow book's own
     running equity;
  4. shadow trades go to their own ledger, never to the SR-D forward ledger.
"""
import numpy as np
import pandas as pd
import pytest

from fabletradebot import shadow
from fabletradebot.config import Params, profile
from fabletradebot.engine import run


def _two_coin_scenario(n=30):
    """BTC and ETH both fire a long at bar0 — one seat, two candidates."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    frames, feats, cands = {}, {}, {}
    for sym, drift in (("BTC", 0.2), ("ETH", 0.15)):
        px = [(100, 100.8, 99.4, 100 + drift * i) for i in range(n)]
        df = pd.DataFrame(px, columns=["open", "high", "low", "close"], index=idx)
        df["volume"] = 1000.0
        f = pd.DataFrame(index=idx)
        f["atr1h"], f["bias4h"] = 1.0, 1.0
        frames[sym], feats[sym] = df, f
        cands[sym] = pd.DataFrame({"dir": [1], "conf": [0.65], "sl": [95.0],
                                   "setup": ["BRK_L"]}, index=[idx[0]])
    regime = pd.DataFrame({"state": "TREND_UP", "btc_dir": 1}, index=idx)
    corr = pd.Series(False, index=idx)
    return idx, (frames, feats, cands, {"BTC": None, "ETH": None}, regime, corr)


def test_size_equity_default_is_inert():
    """The engine field the shadow book needs must not move the live path."""
    _, args = _two_coin_scenario()
    p = Params()
    base = run(*args, p, equity0=10_000.0)
    same = run(*args, replace_size_equity(p, 0.0), equity0=10_000.0)
    assert base["final_equity"] == pytest.approx(same["final_equity"], rel=1e-12)
    assert len(base["trades"]) == len(same["trades"])


def replace_size_equity(p, v):
    from dataclasses import replace
    return replace(p, size_equity=v)


def test_free_seat_takes_the_candidate_the_seat_rejected():
    _, args = _two_coin_scenario()
    p = profile("whale")
    live = run(*args, p, equity0=10_000.0)
    ghost = run(*args, shadow.params(p), equity0=shadow.EQUITY)

    live_syms = set(live["open_positions"]) | set(live["trades"].get("sym", []))
    ghost_syms = set(ghost["open_positions"]) | set(ghost["trades"].get("sym", []))
    assert len(live_syms) == 1, "whale holds exactly one seat"
    assert live_syms < ghost_syms, "the shadow book must take the rejected coin too"


def test_shadow_r_is_independent_of_its_own_equity():
    """Sizing reads the fixed size_equity, so R is a pure per-trade measure."""
    _, args = _two_coin_scenario()
    sp = shadow.params(profile("whale"))
    a = run(*args, sp, equity0=shadow.EQUITY)
    b = run(*args, sp, equity0=shadow.EQUITY * 7)
    ra = sorted(a["trades"]["r"]) if len(a["trades"]) else []
    rb = sorted(b["trades"]["r"]) if len(b["trades"]) else []
    assert np.allclose(ra, rb)


def test_shadow_params_change_only_the_seat_and_account_governors():
    p = profile("whale")
    sp = shadow.params(p)
    lifted = {"max_positions", "max_positions_corr", "max_open_risk",
              "max_margin_frac", "dd_stop", "dd_half", "dd_resume",
              "circuit_loss_24h", "size_equity"}
    changed = {f for f in vars(p) if getattr(p, f) != getattr(sp, f)}
    assert changed == lifted, f"shadow must not alter entry/exit logic: {changed - lifted}"


def test_shadow_ledger_is_separate_from_the_promotion_ledger(tmp_path):
    """SR-D sizes REAL positions off forward_ledger.csv — it must stay clean."""
    from fabletradebot import promotion
    path = tmp_path / "shadow_ledger.csv"
    shadow.append_trade({"sym": "BTC", "setup": "PBK_S", "r": 3.0, "pnl": 1.0},
                        shadow.BLOCKED, path=str(path))
    assert path.exists()
    assert promotion.LEDGER_PATH not in str(path)
    row = pd.read_csv(path).iloc[0]
    assert row["seat_state"] == shadow.BLOCKED
    assert row["setup"] == "PBK_S"
