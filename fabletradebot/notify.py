"""Telegram push notifications (optional).

Fires only when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set; any failure
is logged and swallowed so a notification never breaks the trade loop.
"""
import json
import os
import urllib.parse
import urllib.request


def telegram_enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")
               and os.environ.get("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as exc:  # notifications must never break the trade loop
        print(f"[notify] telegram send failed: {exc}")
        return False


_DIR = {1: "🟢 LONG", -1: "🔴 SHORT", 0: "⚪ FLAT"}
_OUTCOME = {"Win": "✅ WIN", "Loss": "❌ LOSS",
            "Timeout-Win": "⏳ TIMEOUT-WIN", "Timeout-Loss": "⌛ TIMEOUT-LOSS"}


def format_scored_open(pos: dict) -> str:
    """Telegram message for a newly opened scored position."""
    return (
        f"<b>🎯 {pos['system']} OPEN</b> — <b>{pos['asset']}</b> {_DIR[pos['direction']]}\n"
        f"진입 <b>{pos['entry']:.6g}</b>  ·  TP {pos['tp']:.6g} / SL {pos['sl']:.6g}\n"
        f"목표 비중 {pos['weight']:+.3f}  ·  봉 {pos['opened_ts']}"
    )


def format_scored_close(pos: dict) -> str:
    """Telegram message for a resolved scored position."""
    return (
        f"<b>{_OUTCOME.get(pos['status'], pos['status'])}</b> — "
        f"<b>{pos['asset']}</b> {pos['system']} {_DIR[pos['direction']]} "
        f"<b>{pos['result_r']:+.2f}R</b>\n"
        f"청산 {pos['exit']:.6g} (진입 {pos['entry']:.6g})  ·  {pos['closed_ts']}"
    )
