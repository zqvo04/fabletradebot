"""Signal detection + notification/journal wiring: pure logic and the
env-gated no-op contract (integrations must never fire or raise unconfigured)."""
import importlib

from fabletradebot import notify, journal_notion, okx_account, okx_auth
from run_live_v3 import signal_changes


def test_signal_changes_threshold_and_direction():
    prev = {"BTC": 0.0, "ETH": 0.10, "SOL": -0.20, "HYPE": 0.0}
    new = {"BTC": -0.09, "ETH": 0.11, "SOL": 0.05, "HYPE": 0.0}
    out = {s["asset"]: s for s in signal_changes(prev, new, 0.03)}
    assert set(out) == {"BTC", "SOL"}            # ETH moved 0.01 (< thr), HYPE flat
    assert out["BTC"]["direction"] == "SHORT"    # new target negative
    assert out["SOL"]["direction"] == "LONG"     # crossed from short to long
    assert abs(out["SOL"]["delta"] - 0.25) < 1e-9


def test_signal_changes_flat_direction():
    out = signal_changes({"BTC": 0.20}, {"BTC": 0.0}, 0.03)
    assert len(out) == 1 and out[0]["direction"] == "FLAT"


def test_first_fire_from_empty_state():
    out = signal_changes({}, {"BTC": 0.15, "ETH": 0.0}, 0.03)
    assert [s["asset"] for s in out] == ["BTC"]  # nonzero target establishes


def test_telegram_disabled_without_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.telegram_enabled() is False
    assert notify.send_telegram("hi") is False   # no network call, no raise


def test_notion_signal_disabled_without_env(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_SIGNAL_DB_ID", raising=False)
    assert journal_notion.post_signal({"asset": "BTC"}) is False


def test_okx_reads_none_without_keys(monkeypatch):
    for k in okx_auth.KEYS:
        monkeypatch.delenv(k, raising=False)
    assert okx_auth.has_keys() is False
    assert okx_account.fetch_equity() is None
    assert okx_account.fetch_positions() is None


def test_format_signal_renders():
    msg = notify.format_signal(dict(
        system="v3", asset="SOL", direction="LONG", target_weight=0.187,
        prev_weight=0.021, delta=0.166, equity=120004.45,
        bar_time="2026-07-10 08:00:00+00:00"))
    assert "SOL" in msg and "LONG" in msg and "+0.187" in msg
