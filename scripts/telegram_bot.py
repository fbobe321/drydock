#!/usr/bin/env python3
"""DryDock Telegram Bot — check status, run tests, trigger actions.

Commands:
  /status     — Current test results and system status
  /failures   — List top failures with reasons
  /report     — Full failure analysis report
  /rerun      — Restart the parallel test runner
  /version    — Current drydock version
  /release    — Trigger a release now
  /vllm       — Check vLLM model status
  /tests N    — Run test harness on project N (test-only, no rebuild)
  /rebuild N  — Rebuild project N via TUI and test
  /help       — Show available commands
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

BOT_TOKEN = "8488479213:AAGd2tMUrqc-Xse14IQ6yfoMudAAal7odio"
CHAT_ID = 8431425848
PROJECTS_DIR = Path("/data3/drydock_test_projects")
RESULTS_FILE = PROJECTS_DIR / "real_test_results.txt"
RESULTS_JSON = PROJECTS_DIR / "real_test_results.json"
PYTHON = "/home/bobef/miniconda3/bin/python3"
LAST_UPDATE_FILE = Path("/tmp/telegram_bot_last_update")


def send(text: str):
    """Send message to Telegram."""
    # Truncate to Telegram's 4096 char limit
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data, timeout=10,
        )
    except Exception as e:
        # Retry without markdown
        data = urllib.parse.urlencode({
            "chat_id": CHAT_ID,
            "text": text,
        }).encode()
        try:
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data, timeout=10,
            )
        except Exception:
            pass


def get_updates() -> list:
    """Get new messages from Telegram."""
    last_id = 0
    if LAST_UPDATE_FILE.exists():
        try:
            last_id = int(LAST_UPDATE_FILE.read_text().strip())
        except ValueError:
            pass

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_id + 1}&timeout=1"
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        if data.get("ok") and data.get("result"):
            updates = data["result"]
            if updates:
                LAST_UPDATE_FILE.write_text(str(updates[-1]["update_id"]))
            return updates
    except Exception:
        pass
    return []


def cmd_status() -> str:
    """Current test status."""
    version = "?"
    try:
        r = subprocess.run([PYTHON, "-c", "import drydock; print(drydock.__version__)"],
                          capture_output=True, text=True, timeout=5)
        version = r.stdout.strip()
    except Exception:
        pass

    # Test results
    if RESULTS_FILE.exists():
        lines = RESULTS_FILE.read_text().strip().split("\n")
        total = len(lines)
        passed = sum(1 for l in lines if l.endswith("|PASS"))
        failed = sum(1 for l in lines if l.endswith("|FAIL"))
    else:
        total = passed = failed = 0

    # Runner status
    runners = subprocess.run("ps aux | grep -E 'parallel_tests|tui_test' | grep -v grep | wc -l",
                            shell=True, capture_output=True, text=True).stdout.strip()

    # vLLM
    try:
        urllib.request.urlopen("http://localhost:8000/v1/models", timeout=3)
        vllm = "UP"
    except Exception:
        vllm = "DOWN"

    return (
        f"DryDock v{version}\n"
        f"Tests: {passed}/{total} pass ({failed} fail)\n"
        f"Runner: {'active' if int(runners) > 0 else 'stopped'}\n"
        f"vLLM: {vllm}\n"
        f"Remaining: {400 - total} untested"
    )


def cmd_failures() -> str:
    """Top failures."""
    if not RESULTS_FILE.exists():
        return "No results yet"

    failures = []
    for line in RESULTS_FILE.read_text().strip().split("\n"):
        if line.endswith("|FAIL"):
            parts = line.split("|")
            if len(parts) >= 8:
                failures.append(f"  {parts[0]}: files={parts[2]} help={parts[3]} func={parts[4]}")

    if not failures:
        return "No failures!"

    return f"Failures ({len(failures)}):\n" + "\n".join(failures[:15])


def cmd_report() -> str:
    """Full failure report."""
    report_path = PROJECTS_DIR / "failure_report.md"
    if report_path.exists():
        return report_path.read_text()[:4000]
    # Generate fresh
    subprocess.run(["/bin/bash", str(PROJECTS_DIR / "analyze_failures.sh")],
                  timeout=30, capture_output=True)
    if report_path.exists():
        return report_path.read_text()[:4000]
    return "No report available"


def cmd_rerun() -> str:
    """Restart test runner."""
    subprocess.run("pkill -f run_parallel_tests", shell=True, capture_output=True)
    time.sleep(2)

    tested = 0
    if RESULTS_FILE.exists():
        tested = len(RESULTS_FILE.read_text().strip().split("\n"))

    subprocess.Popen(
        f"nohup bash {PROJECTS_DIR}/run_parallel_tests.sh 1 > {PROJECTS_DIR}/parallel_test.log 2>&1 &",
        shell=True,
    )
    return f"Runner restarted. {tested} already tested, continuing from where stopped."


def cmd_version() -> str:
    """Current version."""
    try:
        r = subprocess.run([PYTHON, "-c", "import drydock; print(drydock.__version__)"],
                          capture_output=True, text=True, timeout=5)
        v = r.stdout.strip()

        r2 = subprocess.run(["git", "log", "--oneline", "-5"], capture_output=True, text=True,
                           timeout=5, cwd="/data3/drydock")
        log = r2.stdout.strip()
        return f"DryDock v{v}\n\nRecent commits:\n{log}"
    except Exception as e:
        return f"Error: {e}"


def cmd_release() -> str:
    """Trigger release."""
    try:
        r = subprocess.run(
            ["/bin/bash", "/data3/drydock/scripts/auto_release.sh"],
            capture_output=True, text=True, timeout=300,
        )
        return f"Release result:\n{r.stdout[-500:]}"
    except subprocess.TimeoutExpired:
        return "Release timed out (300s)"
    except Exception as e:
        return f"Release error: {e}"


def cmd_vllm() -> str:
    """vLLM status."""
    try:
        resp = urllib.request.urlopen("http://localhost:8000/v1/models", timeout=3)
        data = json.loads(resp.read())
        model = data["data"][0]["id"]
        return f"vLLM: UP\nModel: {model}\nMax context: {data['data'][0].get('max_model_len', '?')}"
    except Exception as e:
        return f"vLLM: DOWN\nError: {e}"


def cmd_tests(arg: str) -> str:
    """Run test on specific project."""
    try:
        num = int(arg.strip())
        r = subprocess.run(
            [PYTHON, str(PROJECTS_DIR / "test_harness.py"), "--run", str(num), str(num), "--test-only"],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECTS_DIR),
        )
        return r.stdout[-1000:] or r.stderr[-500:] or "No output"
    except ValueError:
        return "Usage: /tests N (project number)"
    except subprocess.TimeoutExpired:
        return "Test timed out"


def cmd_rebuild(arg: str) -> str:
    """Rebuild project via TUI."""
    try:
        num = int(arg.strip())
        r = subprocess.run(
            [PYTHON, str(PROJECTS_DIR / "test_harness.py"), "--run", str(num), str(num), "--rebuild"],
            capture_output=True, text=True, timeout=360,
            cwd=str(PROJECTS_DIR),
        )
        return r.stdout[-1000:] or r.stderr[-500:] or "No output"
    except ValueError:
        return "Usage: /rebuild N (project number)"
    except subprocess.TimeoutExpired:
        return "Rebuild timed out (360s)"


def cmd_help() -> str:
    return (
        "DryDock Bot Commands:\n\n"
        "Status:\n"
        "  /status — Test results & system status\n"
        "  /failures — List top failures\n"
        "  /report — Full failure analysis\n"
        "  /version — Current version + commits\n"
        "  /vllm — Check model server\n\n"
        "Testing:\n"
        "  /tests N — Test project N (no rebuild)\n"
        "  /rebuild N — Rebuild project N via TUI\n"
        "  /rerun — Restart parallel test runner\n\n"
        "Fixes:\n"
        "  /dryfix <issue> — Launch Claude to fix a drydock bug\n"
        "  /fixtest N — Diagnose why project N fails\n\n"
        "Deploy:\n"
        "  /release — Trigger PyPI+GitHub release\n\n"
        "Example:\n"
        "  /dryfix search_replace crashes with missing file_path\n"
        "  /fixtest 42\n"
        "  /rebuild 10"
    )


def cmd_dryfix(arg: str) -> str:
    """Launch Claude Code to fix an issue."""
    if not arg.strip():
        return "Usage: /dryfix <description of the issue>\nExample: /dryfix search_replace crashes when file_path is missing"

    issue = arg.strip()
    send(f"Starting fix for: {issue}\nThis may take a few minutes...")

    # Build a focused prompt
    prompt = (
        f"You are fixing a bug in DryDock (CLI coding agent at /data3/drydock). "
        f"Read CLAUDE.md first for context. The issue reported by the user is:\n\n"
        f"  {issue}\n\n"
        f"Steps:\n"
        f"1. Find the relevant code (grep for keywords from the issue)\n"
        f"2. Read the file and understand the bug\n"
        f"3. Fix it with a minimal edit\n"
        f"4. Run: python3 -c \"import ast; ast.parse(open('file').read())\" to verify syntax\n"
        f"5. Respond with EXACTLY what you changed and why (under 200 words)\n\n"
        f"Do NOT refactor, add features, or change unrelated code. Minimal fix only."
    )

    try:
        r = subprocess.run(
            ["claude", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True, text=True, timeout=300,
            cwd="/data3/drydock",
        )
        output = r.stdout.strip()
        if len(output) > 3500:
            output = output[-3500:]
        return f"Fix attempt complete:\n\n{output}" if output else f"No output (exit {r.returncode})"
    except subprocess.TimeoutExpired:
        return "Fix timed out after 5 minutes"
    except Exception as e:
        return f"Fix error: {e}"


def cmd_fixtest(arg: str) -> str:
    """Fix a specific failing test project."""
    try:
        num = int(arg.strip())
    except ValueError:
        return "Usage: /fixtest N (project number)\nLaunches Claude to diagnose and fix why project N fails"

    # Find the project
    dirs = sorted(PROJECTS_DIR.glob(f"{num:02d}_*")) + sorted(PROJECTS_DIR.glob(f"{num:03d}_*"))
    if not dirs:
        return f"Project {num} not found"

    proj_dir = dirs[0]
    pkg = proj_dir.name.split("_", 1)[1] if "_" in proj_dir.name else proj_dir.name

    send(f"Analyzing failure for {proj_dir.name}...")

    prompt = (
        f"A DryDock test project failed. Diagnose and fix the DryDock agent code (NOT the PRD).\n\n"
        f"Project: {proj_dir.name}\n"
        f"Package: {pkg}\n"
        f"PRD: {proj_dir}/PRD.md\n"
        f"TUI output: {proj_dir}/tui_output.txt\n\n"
        f"Steps:\n"
        f"1. Read the PRD to understand what was requested\n"
        f"2. Read tui_output.txt to see what went wrong\n"
        f"3. Check if the issue is in DryDock code (agent_loop, tools, prompts)\n"
        f"4. If it's a DryDock bug, fix it. If it's a model behavior issue, update gemma4.md\n"
        f"5. Respond with what you found and what you changed (under 200 words)"
    )

    try:
        r = subprocess.run(
            ["claude", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True, text=True, timeout=300,
            cwd="/data3/drydock",
        )
        output = r.stdout.strip()
        if len(output) > 3500:
            output = output[-3500:]
        return f"Analysis complete:\n\n{output}" if output else f"No output (exit {r.returncode})"
    except subprocess.TimeoutExpired:
        return "Analysis timed out after 5 minutes"
    except Exception as e:
        return f"Error: {e}"


COMMANDS = {
    "/status": lambda _: cmd_status(),
    "/failures": lambda _: cmd_failures(),
    "/report": lambda _: cmd_report(),
    "/rerun": lambda _: cmd_rerun(),
    "/version": lambda _: cmd_version(),
    "/release": lambda _: cmd_release(),
    "/vllm": lambda _: cmd_vllm(),
    "/tests": cmd_tests,
    "/rebuild": cmd_rebuild,
    "/dryfix": cmd_dryfix,
    "/fixtest": cmd_fixtest,
    "/help": lambda _: cmd_help(),
}


def process_message(text: str) -> str | None:
    """Process a command and return response."""
    text = text.strip()
    if not text.startswith("/"):
        return None

    parts = text.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    handler = COMMANDS.get(cmd)
    if handler:
        try:
            return handler(arg)
        except Exception as e:
            return f"Error: {e}"
    return None


def poll_once():
    """Check for new messages and respond."""
    updates = get_updates()
    for update in updates:
        msg = update.get("message", {})
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")

        if chat_id != CHAT_ID:
            continue

        response = process_message(text)
        if response:
            send(response)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon, poll every 5s")
    args = parser.parse_args()

    if args.daemon:
        print(f"DryDock Telegram bot running (polling every 5s)...")
        while True:
            try:
                poll_once()
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(5)
    else:
        poll_once()
