"""Notification / journal wiring: env-gated no-op contract (integrations must
never fire or raise unconfigured) + message rendering."""
from fabletradebot import notify, journal_notion, okx_account, okx_auth


def _pos(**kw):
    base = dict(system="v4", asset="SOL", direction=1, entry=152.3, tp=161.0,
                sl=146.1, weight=0.36, opened_ts="2026-07-10T12:00:00+00:00",
                status="Open", exit=None, result_r=None, closed_ts=None)
    base.update(kw)
    return base


def test_telegram_disabled_without_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.telegram_enabled() is False
    assert notify.send_telegram("hi") is False   # no network call, no raise


def test_notion_scored_disabled_without_env(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_SIGNAL_DB_ID", raising=False)
    assert journal_notion.post_scored(_pos()) is None
    assert journal_notion.update_scored("pageid", _pos()) is False


def test_okx_reads_none_without_keys(monkeypatch):
    for k in okx_auth.KEYS:
        monkeypatch.delenv(k, raising=False)
    assert okx_auth.has_keys() is False
    assert okx_account.fetch_equity() is None
    assert okx_account.fetch_positions() is None


def test_format_scored_open_renders():
    msg = notify.format_scored_open(_pos())
    assert "SOL" in msg and "LONG" in msg and "152.3" in msg and "OPEN" in msg


def test_format_scored_close_renders():
    msg = notify.format_scored_close(_pos(status="Win", exit=161.0, result_r=1.33,
                                          closed_ts="2026-07-12T00:00:00+00:00"))
    assert "WIN" in msg and "+1.33R" in msg
    loss = notify.format_scored_close(_pos(direction=-1, status="Loss", exit=146.1,
                                           result_r=-1.0, closed_ts="x"))
    assert "LOSS" in loss and "-1.00R" in loss


def test_leverage_and_pnl_render_when_present():
    op = notify.format_scored_open(_pos(system="v5", leverage=5.0))
    assert "5x" in op and "레버리지" in op
    cl = notify.format_scored_close(_pos(system="v5", status="Win", exit=161.0,
                                         result_r=1.33, result_pct=5.72,
                                         leverage=3.0, closed_ts="x"))
    assert "+5.72%" in cl and "3x" in cl
    # absent leverage/pct -> silently omitted, no crash
    assert "레버리지" not in notify.format_scored_open(_pos())


def test_scored_props_include_leverage_and_pnl():
    props = journal_notion._scored_props(
        _pos(system="v5", status="Win", exit=161.0, result_r=1.33,
             result_pct=5.72, leverage=5.0, equity=100000.0,
             closed_ts="2026-07-12T00:00:00+00:00"))
    assert props["Leverage"]["number"] == 5.0
    assert props["PnL %"]["number"] == 5.72
    assert props["System"]["select"]["name"] == "v5"
