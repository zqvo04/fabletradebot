"""Telegram alerts. Enabled only when TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are
set; failures never break the trade loop."""
from __future__ import annotations

import json
import os
import urllib.request


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    body = json.dumps({"chat_id": chat, "text": text,
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as exc:
        print(f"[notify] telegram failed: {exc}")
        return False


def fmt_entry(pos: dict) -> str:
    d = "LONG" if pos["dir"] > 0 else "SHORT"
    return (f"🎯 V1 ENTRY {pos['sym']} {d} {pos['leverage']:.0f}x\n"
            f"entry {pos['entry']:.6g} | SL {pos['sl']:.6g} | TP1 {pos['tp1']:.6g}\n"
            f"conf {pos['conf']:.2f} | setup {pos['setup']} | regime {pos['regime']}\n"
            f"risk {pos['risk_amt']:.2f} USDT ({pos['risk_pct']:.2f}%)")


def fmt_exit(tr: dict) -> str:
    icon = "✅" if tr["pnl"] > 0 else "❌"
    d = "LONG" if tr["dir"] > 0 else "SHORT"
    return (f"{icon} V1 EXIT {tr['sym']} {d} {tr['leverage']:.0f}x — {tr['reason']}\n"
            f"{tr['r']:+.2f}R | price {tr['pnl_pct_price']:+.2f}% | "
            f"levered {tr['pnl_pct_lev']:+.2f}%\n"
            f"entry {tr['entry']:.6g} → exit {tr['exit']:.6g} | held {tr['bars']}h\n"
            f"equity {tr['equity_after']:.2f}")
