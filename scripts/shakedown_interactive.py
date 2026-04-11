#!/usr/bin/env python3
"""Interactive shakedown — simulates a real user session with back-and-forth.

Unlike the basic shakedown (one prompt → wait), this script sends a
SEQUENCE of prompts that exercise planning, building, testing, editing,
and troubleshooting.  It watches each response before sending the next.

Usage:
    python3 scripts/shakedown_interactive.py \
        --cwd /data3/drydock_test_projects/403_tool_agent \
        --pkg tool_agent
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pexpect

DRYDOCK_BIN = "/home/bobef/miniforge3/envs/drydock/bin/drydock"
SESSION_ROOT = Path.home() / ".vibe" / "logs" / "session"

# ── Conversation scripts per PRD ─────────────────────────────────────
# Each step: (prompt_text, wait_condition, max_wait_seconds)
# wait_condition: "msgs>=N" or "writes>=N" or "done"

SCRIPTS: dict[str, list[tuple[str, str, int]]] = {
    # ── Tool Agent: plan, todo, build, test, extend, troubleshoot ──
    "tool_agent": [
        ("Review the PRD and create a plan. List what files you will create "
         "and in what order. Do NOT write any code yet — just plan.",
         "msgs>=4", 120),
        ("Good plan. Now create a todo list with all the steps needed to "
         "build this package. Include: create files, test, verify each tool. "
         "Then start working through the todo list — build the first 3 files.",
         "writes>=3", 150),
        ("Keep going through your todo list. Write the remaining files. "
         "Update each todo item as you complete it.",
         "writes>=5", 150),
        ("Run python3 -m {pkg} --help to verify the package works. If it "
         "fails, fix it. Mark the test todo as done when it passes.",
         "msgs>={prev}+3", 120),
        ("Explain how the agent loop works. Walk me through what happens when "
         "a user types a query.",
         "msgs>={prev}+2", 90),
        ("Add a new tool called 'date_tool' that returns the current date "
         "and time. Use search_replace on tools.py — do NOT rewrite the whole file.",
         "msgs>={prev}+4", 120),
        ("Test it: run python3 -m {pkg} \"What is the date today?\"",
         "msgs>={prev}+2", 90),
        ("Now test all the original tools still work: "
         "\"What is 23 * 47?\", \"Count words in PRD.md\", \"Search for def in *.py\". "
         "Show me the final todo list status.",
         "done", 180),
    ],

    # ── Stock Screener: todo-driven build + test + feature add ──
    "stock_screener": [
        ("Review the PRD. What modules will you need? Plan the architecture "
         "before writing any code.",
         "msgs>=4", 120),
        ("Create a todo list for the full build: 1) data loader, 2) screener "
         "logic, 3) CLI, 4) formatter, 5) sample data, 6) test all commands, "
         "7) export test. Then start working — build the data loader and screener.",
         "writes>=3", 150),
        ("Continue through the todo list. Create the CLI, formatter, and "
         "__main__.py. Make sure the package can run. Update todos as you go.",
         "writes>=6", 150),
        ("Create a sample portfolio.csv with at least 8 stocks for testing.",
         "msgs>={prev}+2", 90),
        ("Test: run python3 -m {pkg} screen --data portfolio.csv --pb-max 1.5",
         "msgs>={prev}+2", 90),
        ("The output formatting looks basic. Update the formatter to align "
         "columns properly using string formatting. Use search_replace.",
         "msgs>={prev}+4", 120),
        ("Test the rank command: python3 -m {pkg} rank --data portfolio.csv --top 5",
         "msgs>={prev}+2", 90),
        ("Export results to JSON and show me the file contents. "
         "Show me the final todo list — everything should be done.",
         "done", 120),
    ],

    # ── Eval Harness: todo-driven build + dataset + eval + improve ──
    "eval_harness": [
        ("Review the PRD and plan. How will you implement the code evaluator "
         "safely without using eval()? Think through the design.",
         "msgs>=4", 120),
        ("Create a todo list for the full build. Then start — build the "
         "evaluators module first with exact match, fuzzy match, and contains.",
         "writes>=2", 150),
        ("Continue through the todo list. Build runner.py, report.py, cli.py, "
         "and __main__.py. Update todos as you finish each file.",
         "writes>=6", 150),
        ("Create a sample tasks.json with 10 test cases — mix of math, "
         "text, and logic questions.",
         "msgs>={prev}+2", 90),
        ("Run: python3 -m {pkg} run --dataset tasks.json --evaluator exact",
         "msgs>={prev}+3", 120),
        ("The exact evaluator is too strict. Add a 'normalize' option that "
         "strips whitespace and lowercases before comparing. Use search_replace "
         "to modify evaluators.py.",
         "msgs>={prev}+4", 120),
        ("Run the eval again and show me the report. Are the results better?",
         "msgs>={prev}+3", 120),
        ("What's the overall accuracy? Which tasks are failing and why? "
         "Show me the final todo list status.",
         "done", 120),
    ],

    # ── Doc QA: todo-driven build + ingest + query + debug ──
    "doc_qa": [
        ("Review the PRD. This is a TF-IDF retrieval system with no external "
         "deps. How will you implement cosine similarity with just stdlib? Plan first.",
         "msgs>=4", 120),
        ("Create a todo list for the build: 1) ingestion, 2) chunking, "
         "3) TF-IDF index, 4) query engine, 5) CLI, 6) test data, 7) test "
         "ingest, 8) test query. Then start — build the ingestion module.",
         "writes>=2", 150),
        ("Continue through the todo list. Build the TF-IDF index module "
         "and query processor. Update todos as you complete each.",
         "writes>=4", 150),
        ("Build the CLI and __main__.py. Make sure python3 -m {pkg} --help works.",
         "writes>=6", 120),
        ("Create a test_docs/ folder with 3 small .txt files about different topics.",
         "msgs>={prev}+2", 90),
        ("Run: python3 -m {pkg} ingest test_docs/",
         "msgs>={prev}+2", 90),
        ("Query: python3 -m {pkg} query \"What is the main topic?\"",
         "msgs>={prev}+2", 90),
        ("The retrieval results look off. Read the index.py file and explain "
         "the TF-IDF calculation. Is the IDF formula correct? "
         "Show me the final todo list.",
         "done", 120),
    ],

    # ── Prompt Optimizer: todo-driven build + dataset + optimize + improve ──
    "prompt_optimizer": [
        ("Review the PRD. The default executor uses keyword matching — explain "
         "how you'll implement that without an LLM. Plan the architecture.",
         "msgs>=4", 120),
        ("Create a todo list for the full build. Then start — build the "
         "template generation and mutation module first.",
         "writes>=2", 150),
        ("Continue through the todo list. Build the executor, scorer, and "
         "optimizer loop. Update todos as you go.",
         "writes>=5", 150),
        ("Build the CLI and create a sample sentiment dataset with 15 examples.",
         "writes>=7", 120),
        ("Run: python3 -m {pkg} run --task sentiment --dataset data.json --iterations 3",
         "msgs>={prev}+3", 120),
        ("Show me the optimization history. Is the score improving across iterations?",
         "msgs>={prev}+2", 90),
        ("The keyword matching is too simple. Add support for negation words "
         "like 'not good' = negative. Use search_replace on executor.py.",
         "msgs>={prev}+4", 120),
        ("Run the optimization again and compare results to the previous run. "
         "Show me the final todo list — everything should be marked done.",
         "done", 120),
    ],
}

# Default fallback script for unknown packages
DEFAULT_SCRIPT: list[tuple[str, str, int]] = [
    ("Review the PRD and create a plan. List what files you need.",
     "msgs>=4", 120),
    ("Start building — write the core modules first.",
     "writes>=3", 120),
    ("Continue — write the remaining files.",
     "writes>=5", 150),
    ("Test: run python3 -m {pkg} --help",
     "msgs>={prev}+2", 90),
    ("Run the main functionality and check for errors.",
     "done", 120),
]


class SessionWatcher:
    """Lightweight session log poller."""

    def __init__(self, cwd: Path, since: float):
        self.cwd = cwd.resolve()
        self.since = since
        self.session_dir: Path | None = None
        self.messages: list[dict] = []

    def find_session(self) -> Path | None:
        if self.session_dir is not None:
            return self.session_dir
        for entry in sorted(SESSION_ROOT.iterdir(), reverse=True):
            try:
                if not entry.is_dir():
                    continue
                if entry.stat().st_mtime < self.since - 5:
                    continue
                meta = json.loads((entry / "meta.json").read_text())
                wd = meta.get("environment", {}).get("working_directory", "")
                if str(self.cwd) == wd:
                    self.session_dir = entry
                    return entry
            except Exception:
                continue
        return None

    def refresh(self) -> int:
        sd = self.find_session()
        if sd is None:
            return 0
        msgs: list[dict] = []
        for msg_file in sorted(sd.rglob("messages.jsonl")):
            try:
                for line in msg_file.read_text().strip().split("\n"):
                    if line.strip():
                        msgs.append(json.loads(line))
            except Exception:
                continue
        self.messages = msgs
        return len(msgs)

    def count_writes(self) -> int:
        n = 0
        for m in self.messages:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    name = tc.get("function", {}).get("name", "")
                    if name in ("write_file", "search_replace"):
                        n += 1
        return n

    def has_errors(self) -> list[str]:
        errors = []
        for m in self.messages:
            if m.get("role") == "tool":
                c = str(m.get("content", ""))
                if "<tool_error>" in c:
                    errors.append(c[:120])
        return errors

    def model_said_done(self) -> bool:
        if not self.messages:
            return False
        last = self.messages[-1]
        if last.get("role") == "assistant":
            tc = last.get("tool_calls", [])
            content = last.get("content", "") or ""
            if not tc and content.strip():
                return True
        return False


def type_message(child: pexpect.spawn, text: str) -> None:
    for ch in text:
        child.send(ch)
        time.sleep(0.01)
    time.sleep(0.2)
    child.send("\r")


def drain_pty(child: pexpect.spawn, seconds: float = 2.0) -> None:
    """Read PTY output to prevent buffer deadlock."""
    cycles = int(seconds / 0.1)
    for _ in range(cycles):
        try:
            child.expect(pexpect.TIMEOUT, timeout=0.1)
        except pexpect.EOF:
            break


def check_condition(cond: str, watcher: SessionWatcher, prev_msgs: int) -> bool:
    cond = cond.replace("{prev}", str(prev_msgs))
    if cond == "done":
        return watcher.model_said_done()
    m = re.match(r"(msgs|writes|tool_calls)([><=]+)(\d+)", cond)
    if not m:
        return False
    metric, op, val = m.group(1), m.group(2), int(m.group(3))
    if metric == "msgs":
        actual = len(watcher.messages)
    elif metric == "writes":
        actual = watcher.count_writes()
    else:
        actual = len(watcher.messages)  # fallback
    if op == ">=":
        return actual >= val
    if op == ">":
        return actual > val
    return actual == val


def run_interactive(cwd: Path, pkg: str) -> int:
    print(f"\n{'='*60}")
    print(f"  INTERACTIVE SHAKEDOWN: {pkg}")
    print(f"  cwd: {cwd}")
    print(f"{'='*60}\n")

    # Clean up
    master = cwd / "PRD.master.md"
    target = cwd / "PRD.md"
    if master.exists():
        shutil.copy2(master, target)
    pkg_dir = cwd / pkg
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)

    log_path = Path(f"/tmp/shakedown_interactive_{int(time.time())}.tui.log")
    start_time = time.time()

    env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"}
    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "w")
    child.expect([r">", r"Drydock", r"┌"], timeout=30)
    time.sleep(2)

    # Dismiss trust dialog if present
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.2)
            child.send("\r")
            time.sleep(2)
    except Exception:
        pass

    watcher = SessionWatcher(cwd, since=start_time)
    results: list[dict] = []

    # Pick the conversation script for this package
    script = SCRIPTS.get(pkg, DEFAULT_SCRIPT)
    print(f"  Using script: {'custom' if pkg in SCRIPTS else 'default'} ({len(script)} steps)")

    try:
        for step_idx, (prompt_template, condition, max_wait) in enumerate(script):
            prompt = prompt_template.replace("{pkg}", pkg)

            print(f"\n--- Step {step_idx + 1}/{len(script)} ---")
            print(f"  PROMPT: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
            print(f"  WAIT:   {condition} (max {max_wait}s)")

            type_message(child, prompt)
            time.sleep(1)

            # On first step, wait for the session to actually appear
            # before counting messages. The TUI takes a few seconds to
            # create the session directory.
            if step_idx == 0:
                print("  Waiting for session to appear...", end="", flush=True)
                for _ in range(30):  # up to 30s
                    drain_pty(child)
                    if watcher.find_session():
                        break
                if watcher.find_session():
                    print(f" found: {watcher.session_dir.name}")
                else:
                    print(" not found (continuing anyway)")

            prev_msgs = len(watcher.messages)

            # Wait for condition
            step_start = time.time()
            met = False
            last_report = 0
            while time.time() - step_start < max_wait:
                drain_pty(child)
                if not child.isalive():
                    print(f"  [TUI exited]")
                    break

                watcher.refresh()
                elapsed = int(time.time() - step_start)

                if elapsed - last_report >= 10:
                    msgs = len(watcher.messages)
                    writes = watcher.count_writes()
                    errors = len(watcher.has_errors())
                    print(f"  [{elapsed:3d}s] msgs={msgs} writes={writes} errors={errors}")
                    last_report = elapsed

                if check_condition(condition, watcher, prev_msgs):
                    met = True
                    elapsed = int(time.time() - step_start)
                    print(f"  [{elapsed:3d}s] Condition met!")
                    break

                # Check for dead silence (model not responding at all)
                if elapsed > 120 and len(watcher.messages) == prev_msgs:
                    print(f"  [{elapsed:3d}s] Dead silence — model not responding")
                    break

            step_result = {
                "step": step_idx + 1,
                "prompt": prompt[:80],
                "condition": condition,
                "met": met,
                "elapsed": int(time.time() - step_start),
                "msgs_after": len(watcher.messages),
                "writes_after": watcher.count_writes(),
                "errors": watcher.has_errors()[-3:],  # last 3 errors
            }
            results.append(step_result)

            if not met:
                print(f"  TIMEOUT: condition not met after {max_wait}s")
                # Continue anyway — don't stop the whole test

            time.sleep(2)  # brief pause between steps

    finally:
        try:
            child.sendcontrol("c")
            time.sleep(0.5)
            child.terminate(force=True)
        except Exception:
            pass

    # ── Report ──────────────────────────────────────────────────────
    total_elapsed = int(time.time() - start_time)
    print(f"\n{'='*60}")
    print(f"  RESULTS: {pkg}")
    print(f"  Total time: {total_elapsed}s")
    print(f"  Messages: {len(watcher.messages)}")
    print(f"  Writes: {watcher.count_writes()}")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    for r in results:
        status = "PASS" if r["met"] else "FAIL"
        if r["met"]:
            passed += 1
        else:
            failed += 1
        print(f"\n  Step {r['step']}: {status} ({r['elapsed']}s)")
        print(f"    {r['prompt']}")
        if r["errors"]:
            print(f"    Errors:")
            for e in r["errors"]:
                print(f"      {e[:100]}")

    print(f"\n  Steps: {passed}/{len(script)} passed")
    print(f"  TUI log: {log_path}")
    if watcher.session_dir:
        print(f"  Session: {watcher.session_dir}/messages.jsonl")

    # Save full results
    results_path = Path(f"/tmp/shakedown_interactive_{pkg}_results.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results: {results_path}")

    # Final package check
    try:
        check = subprocess.run(
            ["python3", "-m", pkg, "--help"],
            cwd=str(cwd), capture_output=True, text=True, timeout=10,
        )
        pkg_works = check.returncode == 0 and len(check.stdout.strip()) > 0
    except Exception:
        pkg_works = False
    print(f"\n  Package works: {'YES' if pkg_works else 'NO'}")
    print(f"{'='*60}\n")

    return 0 if failed == 0 and pkg_works else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--pkg", required=True)
    args = parser.parse_args()
    cwd = Path(args.cwd).resolve()
    return run_interactive(cwd, args.pkg)


if __name__ == "__main__":
    sys.exit(main())
