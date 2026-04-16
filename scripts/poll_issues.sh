#!/bin/bash
# Hourly poller: notifies via Telegram about NEW open issues opened by
# fbobe321 in fbobe321/drydock since the last poll. Runs Mon-Fri 7am-5pm
# via cron. State pointer at ~/.drydock/last_issue_poll keeps track of
# which issues we've already alerted on so we don't re-notify.
#
# Cron line (added separately to crontab):
#   0 7-17 * * 1-5  /data3/drydock/scripts/poll_issues.sh >> /data3/drydock/logs/poll_issues.log 2>&1
set -euo pipefail

PYTHON="/home/bobef/miniconda3/bin/python3"
TOKEN_FILE="$HOME/.config/drydock/github_token"
STATE_FILE="$HOME/.drydock/last_issue_poll.json"
DRYDOCK="/data3/drydock"

if [ ! -f "$TOKEN_FILE" ]; then
    echo "[$(date)] ERROR: GitHub token missing at $TOKEN_FILE" >&2
    exit 1
fi

mkdir -p "$(dirname "$STATE_FILE")"

$PYTHON - <<'PYEOF'
"""Find new issues opened by fbobe321 since last poll, notify Telegram."""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "fbobe321/drydock"
AUTHOR = "fbobe321"
TOKEN = Path.home().joinpath(".config/drydock/github_token").read_text().strip()
STATE = Path.home().joinpath(".drydock/last_issue_poll.json")
NOTIFY_SCRIPT = "/data3/drydock/scripts/notify_release.py"


def gh_get(url: str):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            pass
    return {"seen_issue_numbers": []}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def notify_telegram(text: str) -> None:
    """Use the existing release notifier in 'status' mode (plain text)."""
    import subprocess
    subprocess.run(
        [sys.executable, NOTIFY_SCRIPT, "status", text],
        check=False, timeout=10,
    )


state = load_state()
seen = set(state.get("seen_issue_numbers", []))

issues = gh_get(
    f"https://api.github.com/repos/{REPO}/issues"
    f"?state=open&creator={AUTHOR}&per_page=50&sort=created&direction=desc"
)

new_items = []
for it in issues:
    if "pull_request" in it:
        continue
    n = it["number"]
    if n in seen:
        continue
    new_items.append(it)
    seen.add(n)

if new_items:
    lines = [f"⚓ {len(new_items)} new drydock issue(s) from fbobe321:"]
    for it in new_items[:5]:
        lines.append(f"  #{it['number']}: {it['title'][:80]}")
    if len(new_items) > 5:
        lines.append(f"  ...and {len(new_items) - 5} more")
    lines.append(f"  https://github.com/{REPO}/issues")
    notify_telegram("\n".join(lines))
    print(f"[{datetime.now(timezone.utc).isoformat()}] notified {len(new_items)} new issues")
else:
    print(f"[{datetime.now(timezone.utc).isoformat()}] no new issues")

state["seen_issue_numbers"] = sorted(seen)
state["last_poll"] = datetime.now(timezone.utc).isoformat()
save_state(state)
PYEOF
