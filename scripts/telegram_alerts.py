"""
scripts/telegram_alerts.py — Live Telegram trade alerts.

Add to .env:
  TELEGRAM_BOT_TOKEN=123456789:ABC-your-bot-token
  TELEGRAM_CHAT_ID=-1001234567890    # group/channel, or your numeric user ID

How to get these:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Add the bot to your group, then GET https://api.telegram.org/bot<TOKEN>/getUpdates
     to find the chat_id (negative number = group)
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_alert(message: str) -> None:
    """Send a Markdown message to the configured Telegram chat. Never raises."""
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        url     = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id":    _CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # never crash the bot over an alert failure


def test_alert() -> None:
    """Quick test — run this file directly to verify the connection works."""
    send_alert("*Bot Online*\nTelegram alerts are working correctly.")
    print(f"Alert sent (TOKEN={'SET' if _TOKEN else 'NOT SET'}, CHAT_ID={'SET' if _CHAT_ID else 'NOT SET'})")


if __name__ == "__main__":
    test_alert()
