"""Notion/Telegram integrations must fail LOUDLY, not silently, when the
required secrets are missing/empty — a GitHub Actions log showing
NOTION_TOKEN: *** only proves the env var was declared, not that the repo
secret has a real value, so silent disablement is indistinguishable from a
working integration in the log (the root cause of the "OPEN in the GH log,
nothing in Notion" report)."""
import pytest

from fabletradebot import journal_notion, notify


@pytest.fixture(autouse=True)
def _clear_secrets(monkeypatch):
    for var in ("NOTION_TOKEN", "NOTION_SIGNAL_DB_ID",
               "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)


def test_notion_disabled_prints_diagnostic(capsys):
    assert journal_notion.post_open({"sym": "BTC", "dir": 1, "leverage": 5.0,
                                     "entry": 100.0, "sl": 95.0, "tp1": 0.0,
                                     "conf": 0.7, "equity": 10_000.0,
                                     "opened": "2026-01-01", "setup": "S",
                                     "regime": "TREND_UP"}) is None
    out = capsys.readouterr().out
    assert "[journal] Notion disabled" in out
    assert "NOTION_TOKEN" in out and "NOTION_SIGNAL_DB_ID" in out


def test_notion_disabled_with_only_one_var_set_reports_the_missing_one(capsys, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "tok")   # DB id still missing
    assert journal_notion.post_close({"reason": "SL", "pnl": -1.0, "exit": 1,
                                      "r": -1, "pnl_pct_price": -1, "pnl_pct_lev": -1,
                                      "bars": 1, "equity_after": 1, "closed": "x",
                                      "setup": "S", "regime": "R", "dir": 1,
                                      "sym": "BTC", "entry": 1, "leverage": 1,
                                      "conf": 0.5, "opened": "x"}, None) is None
    out = capsys.readouterr().out
    assert "NOTION_SIGNAL_DB_ID" in out
    assert "NOTION_TOKEN" not in out.split("missing/empty env var(s):")[1]


def test_telegram_disabled_prints_diagnostic(capsys):
    assert notify.send("hello") is False
    out = capsys.readouterr().out
    assert "[notify] Telegram disabled" in out
    assert "TELEGRAM_BOT_TOKEN" in out and "TELEGRAM_CHAT_ID" in out


def test_regime_transitions_only_from_start_and_formatted():
    import pandas as pd
    from fabletradebot import regime
    idx = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    states = {"BTC": pd.Series(["RANGE", "TREND_UP", "TREND_UP", "CRISIS"], index=idx),
              "ETH": pd.Series(["RANGE"] * 4, index=idx)}
    ch = regime.transitions(states, idx[2])
    assert ch == [(idx[3], "BTC", "TREND_UP", "CRISIS")]   # idx[1] change is before start
    assert regime.transitions(states, idx[0]) == [(idx[1], "BTC", "RANGE", "TREND_UP"), ch[0]]
    msg = notify.fmt_regime(ch)
    assert "BTC: TREND_UP → CRISIS ⚠️" in msg and "진입 신호 아님" in msg
