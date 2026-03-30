#!/usr/bin/env python3
"""Send DryDock release notifications via Telegram."""
import sys
import urllib.request
import urllib.parse

BOT_TOKEN = "8488479213:AAGd2tMUrqc-Xse14IQ6yfoMudAAal7odio"
CHAT_ID = 8431425848


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        version = sys.argv[1]
        summary = sys.argv[2]
    else:
        version = sys.argv[1] if len(sys.argv) > 1 else "unknown"
        summary = "New release"

    msg = f"⚓ *DryDock v{version}* released\n\n{summary}\n\n`pip install --upgrade drydock-cli`"
    send_telegram(msg)
    print(f"Telegram notification sent for v{version}")
