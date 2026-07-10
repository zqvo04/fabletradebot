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


def format_signal(sig: dict) -> str:
    """Render one signal dict (see run_live_v3) as an HTML Telegram message."""
    arrow = {"LONG": "🟢 LONG", "SHORT": "🔴 SHORT", "FLAT": "⚪ FLAT"}[sig["direction"]]
    return (
        f"<b>⚡ {sig['system']} 신호</b> — <b>{sig['asset']}</b> {arrow}\n"
        f"목표 비중 <b>{sig['target_weight']:+.3f}</b> "
        f"(이전 {sig['prev_weight']:+.3f}, Δ {sig['delta']:+.3f})\n"
        f"자본 ${sig['equity']:,.0f}  ·  봉 {sig['bar_time']}"
    )
