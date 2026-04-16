#!/usr/bin/env python3
"""Send DryDock release notifications via Telegram."""
import sys
import urllib.request
import urllib.parse

BOT_TOKEN = "8488479213:AAGd2tMUrqc-Xse14IQ6yfoMudAAal7odio"
CHAT_ID = 8431425848


def _escape_markdown(text: str) -> str:
    """Escape the Telegram legacy-Markdown special chars that commonly
    appear in commit messages: _ * ` [ ]. Telegram's 400 Bad Request
    response fires when these are unbalanced."""
    for ch in ("_", "*", "`", "[", "]"):
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram(message: str, parse_mode: str = "Markdown"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": CHAT_ID,
        "text": message,
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    data = urllib.parse.urlencode(params).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")
        # Retry in plain text if Markdown parse failed — this fires when
        # a commit message has unbalanced underscores from identifiers
        # like `write_file` or `search_replace`.
        if parse_mode:
            fallback_params = {"chat_id": CHAT_ID, "text": message}
            fallback_data = urllib.parse.urlencode(fallback_params).encode()
            try:
                urllib.request.urlopen(url, fallback_data, timeout=10)
                print("Retried in plain text — delivered.")
            except Exception as e2:
                print(f"Plain-text retry also failed: {e2}")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        version = sys.argv[1]
        summary = sys.argv[2]
    else:
        version = sys.argv[1] if len(sys.argv) > 1 else "unknown"
        summary = "New release"

    if version == "status":
        # Hourly status — plain text, no Markdown parsing issues
        msg = f"⚓ {summary}"
        send_telegram(msg, parse_mode="")
    else:
        msg = f"⚓ *DryDock v{version}* released\n\n{summary}\n\n`pip install --upgrade drydock-cli`"
        send_telegram(msg)
    print(f"Telegram notification sent for v{version}")
