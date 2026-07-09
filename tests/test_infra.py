"""Safety rails: journal and execution must be inert unless explicitly armed."""
import os
from unittest import mock

from fabletradebot.journal_notion import post_trade
from fabletradebot.okx_exec import place_market_order, _live_enabled


def test_notion_journal_inert_without_env():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert post_trade({"asset": "BTC", "playbook": "P1", "direction": 1,
                           "r": 1.0, "pnl": 10.0, "reason": "target",
                           "closed_ts": "2026-07-01"}) is False


def test_live_orders_dry_run_unless_fully_armed():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert place_market_order("BTC", "buy", 1.0) is None
    # keys present but no LIVE_CONFIRM -> still dry
    env = {"TRADE_MODE": "live", "OKX_API_KEY": "k", "OKX_API_SECRET": "s",
           "OKX_PASSPHRASE": "p"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert not _live_enabled()
        assert place_market_order("BTC", "sell", 1.0) is None
