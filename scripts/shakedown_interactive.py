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

    # ═══════════════════════════════════════════════════════════════
    # Tool Agent — 24 steps
    # ═══════════════════════════════════════════════════════════════
    "tool_agent": [
        # ── Phase 1: Planning ──
        ("Review the PRD and create a plan. List what files you will "
         "create and in what order. Do NOT write any code yet.",
         "msgs>=4", 120),
        ("What design patterns will you use for the tool registry? "
         "How will the agent decide which tool to call?",
         "msgs>={prev}+2", 90),
        ("Create a todo list with ALL steps: create each file, test "
         "--help, test each tool individually, add a feature, final "
         "verification. Then start executing — do NOT stop between items.",
         "writes>=2", 150),
        # ── Phase 2: Building ──
        ("Continue through the todo list. Write all remaining files.",
         "writes>=5", 150),
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        ("Run python3 -m {pkg} --list-tools and show me what's available.",
         "msgs>={prev}+2", 90),
        # ── Phase 3: Testing each tool ──
        ("Test the calculator: python3 -m {pkg} \"What is 23 * 47?\"",
         "msgs>={prev}+2", 90),
        ("Test the file reader: python3 -m {pkg} \"Read PRD.md\"",
         "msgs>={prev}+2", 90),
        ("Test the word counter: python3 -m {pkg} \"Count words in PRD.md\"",
         "msgs>={prev}+2", 90),
        ("Test the grep tool: python3 -m {pkg} \"Search for class in *.py\"",
         "msgs>={prev}+2", 90),
        # ── Phase 4: Code review ──
        ("Read agent.py and explain the tool selection logic. Is it "
         "robust? What happens if no tool matches?",
         "msgs>={prev}+2", 120),
        ("Read tools.py. Are there any edge cases in the calculator "
         "that could crash? What about division by zero?",
         "msgs>={prev}+2", 90),
        # ── Phase 5: Feature addition (exercises search_replace) ──
        ("Add a new tool called 'date_tool' that returns the current "
         "date and time. Use search_replace on tools.py — do NOT "
         "rewrite the whole file.",
         "msgs>={prev}+4", 120),
        ("Test it: python3 -m {pkg} \"What is today's date?\"",
         "msgs>={prev}+2", 90),
        ("Add another tool called 'env_tool' that shows environment "
         "variables. Use search_replace again.",
         "msgs>={prev}+4", 120),
        ("Test: python3 -m {pkg} \"Show me the PATH variable\"",
         "msgs>={prev}+2", 90),
        # ── Phase 6: Bug hunt ──
        ("What happens if I pass an empty query? Try: python3 -m {pkg} \"\"",
         "msgs>={prev}+2", 90),
        ("Fix any crash from the empty query. Use search_replace.",
         "msgs>={prev}+3", 120),
        # ── Phase 7: Refactoring ──
        ("The tool descriptions are hardcoded. Can you add a --verbose "
         "flag that shows the reasoning steps? Use search_replace on cli.py.",
         "msgs>={prev}+4", 120),
        ("Test verbose mode: python3 -m {pkg} --verbose \"What is 5+3?\"",
         "msgs>={prev}+2", 90),
        # ── Phase 8: Ideas and wrap-up ──
        ("What features would you add next? Give me 5 ideas for "
         "improving this agent.",
         "msgs>={prev}+2", 90),
        ("Run all the tests one more time — calculator, file reader, "
         "word counter, grep, date, env. Report which pass and fail.",
         "msgs>={prev}+3", 150),
        ("Update the todo list — mark everything done. Show final status.",
         "msgs>={prev}+2", 90),
        ("Write a brief README.md for the package describing what it "
         "does and how to use it.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Stock Screener — 24 steps
    # ═══════════════════════════════════════════════════════════════
    "stock_screener": [
        # ── Planning ──
        ("Review the PRD. What modules will you need? Think about the "
         "data flow: CSV → filter → rank → format → output.",
         "msgs>=4", 120),
        ("How will you handle missing or malformed data in the CSV? "
         "What about stocks with no book value (division by zero)?",
         "msgs>={prev}+2", 90),
        ("Create a comprehensive todo list and start executing it. "
         "Build ALL files without stopping between items.",
         "writes>=3", 150),
        # ── Building ──
        ("Continue. Write CLI, formatter, __main__.py. Don't stop.",
         "writes>=6", 150),
        ("Create a sample portfolio.csv with 10 stocks — include some "
         "edge cases: missing values, zero book_value, negative debt.",
         "msgs>={prev}+2", 90),
        # ── Testing commands ──
        ("Test: python3 -m {pkg} screen --data portfolio.csv --pb-max 1.5",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} screen --data portfolio.csv --insider-min 10",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} rank --data portfolio.csv --top 5",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} export --data portfolio.csv --format json --output results.json",
         "msgs>={prev}+2", 90),
        ("Read results.json and verify the data looks correct.",
         "msgs>={prev}+2", 90),
        # ── Code review ──
        ("Read screener.py. Explain the ranking algorithm. Is the "
         "composite score calculation correct?",
         "msgs>={prev}+2", 120),
        ("Read data.py. How does it handle the edge cases in the CSV?",
         "msgs>={prev}+2", 90),
        # ── Feature: new filter ──
        ("Add a --cap-max filter for maximum market cap. Use "
         "search_replace on screener.py and cli.py.",
         "msgs>={prev}+4", 120),
        ("Test: python3 -m {pkg} screen --data portfolio.csv --cap-max 500000",
         "msgs>={prev}+2", 90),
        # ── Feature: summary stats ──
        ("Add a 'summary' subcommand that shows: total stocks, average "
         "P/B, average debt ratio, highest insider %. Use search_replace.",
         "msgs>={prev}+4", 120),
        ("Test: python3 -m {pkg} summary --data portfolio.csv",
         "msgs>={prev}+2", 90),
        # ── Bug hunt ──
        ("What happens with an empty CSV? Create empty.csv with just "
         "headers and test: python3 -m {pkg} screen --data empty.csv",
         "msgs>={prev}+3", 120),
        ("Fix any crash. Use search_replace.",
         "msgs>={prev}+3", 120),
        # ── Formatting ──
        ("The table output needs better alignment. Update formatter.py "
         "to right-align numbers and left-align text. search_replace.",
         "msgs>={prev}+4", 120),
        ("Test the improved formatting with the rank command.",
         "msgs>={prev}+2", 90),
        # ── Ideas ──
        ("What would a real quantitative analyst want from this tool? "
         "Give me 5 feature ideas.",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Run through all commands one final time: screen, rank, "
         "export, summary. Report results.",
         "msgs>={prev}+3", 150),
        ("Update the todo list — everything should be done.",
         "msgs>={prev}+2", 90),
        ("Write a README.md for the package.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Eval Harness — 24 steps
    # ═══════════════════════════════════════════════════════════════
    "eval_harness": [
        # ── Planning ──
        ("Review the PRD and plan. How will you implement the code "
         "evaluator safely without using eval()? Think through it.",
         "msgs>=4", 120),
        ("What's your strategy for the fuzzy evaluator? How will you "
         "handle partial matches vs completely wrong answers?",
         "msgs>={prev}+2", 90),
        ("Create a todo list. Then build evaluators.py with exact, "
         "fuzzy, contains, and code evaluators. Execute without stopping.",
         "writes>=2", 150),
        # ── Building ──
        ("Continue the todo list. Build runner, report, CLI, __main__.",
         "writes>=6", 150),
        ("Create tasks.json with 15 test cases: 5 math, 5 text, 5 logic.",
         "msgs>={prev}+2", 90),
        # ── Testing evaluators ──
        ("Run: python3 -m {pkg} run --dataset tasks.json --evaluator exact",
         "msgs>={prev}+3", 120),
        ("Run: python3 -m {pkg} run --dataset tasks.json --evaluator fuzzy --threshold 0.8",
         "msgs>={prev}+3", 120),
        ("Run: python3 -m {pkg} run --dataset tasks.json --evaluator contains",
         "msgs>={prev}+3", 120),
        # ── Code review ──
        ("Read evaluators.py. Walk me through each evaluator. Are "
         "there any bugs in the fuzzy matching logic?",
         "msgs>={prev}+2", 120),
        ("Read runner.py. How does it handle exceptions in individual "
         "tasks? Does a single failure crash the whole run?",
         "msgs>={prev}+2", 90),
        # ── Feature: normalize evaluator ──
        ("The exact evaluator fails on '42' vs ' 42 ' and 'Yes' vs "
         "'yes'. Add a normalize option. Use search_replace on evaluators.py.",
         "msgs>={prev}+4", 120),
        ("Test: run the exact evaluator again — are more tasks passing?",
         "msgs>={prev}+3", 120),
        # ── Feature: report improvements ──
        ("The report is too basic. Add per-evaluator comparison: run "
         "all evaluators and show a side-by-side accuracy table. "
         "Use search_replace on report.py.",
         "msgs>={prev}+4", 120),
        ("Generate the comparison report.",
         "msgs>={prev}+2", 90),
        # ── Feature: new task types ──
        ("Add support for 'regex' type tasks where the expected field "
         "is a regex pattern. Use search_replace on evaluators.py.",
         "msgs>={prev}+4", 120),
        ("Add 3 regex tasks to tasks.json and test them.",
         "msgs>={prev}+3", 120),
        # ── Bug hunt ──
        ("What happens with a malformed tasks.json? Create bad.json "
         "with invalid JSON and test: python3 -m {pkg} run --dataset bad.json",
         "msgs>={prev}+3", 120),
        ("Fix any crash. The tool should report the error gracefully.",
         "msgs>={prev}+3", 120),
        # ── Edge cases ──
        ("Test with an empty dataset: create empty.json with [] and "
         "run the evaluator. Should show 0 tasks, 0% accuracy.",
         "msgs>={prev}+3", 120),
        # ── Ideas ──
        ("What metrics would make this harness more useful? Think "
         "about what ML engineers actually need.",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Run the full evaluation suite one more time with all "
         "evaluators. Show the final comparison.",
         "msgs>={prev}+3", 150),
        ("Read the final report output. Does it look professional?",
         "msgs>={prev}+2", 90),
        ("Update the todo list — show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md for the package.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Doc QA — 24 steps
    # ═══════════════════════════════════════════════════════════════
    "doc_qa": [
        # ── Planning ──
        ("Review the PRD. This is a TF-IDF retrieval system with no "
         "external deps. How will you implement cosine similarity "
         "with just stdlib? Plan the math.",
         "msgs>=4", 120),
        ("Walk me through the chunking strategy. How will you handle "
         "overlap between chunks? What about very short documents?",
         "msgs>={prev}+2", 90),
        ("Create a todo list covering: ingestion, chunking, TF-IDF "
         "indexing, query, CLI, test data, test ingest, test query, "
         "add features, final test. Start building immediately.",
         "writes>=2", 150),
        # ── Building ──
        ("Continue the todo list. Build index.py and query.py.",
         "writes>=4", 150),
        ("Build cli.py and __main__.py. Verify --help works.",
         "writes>=6", 120),
        # ── Test data ──
        ("Create test_docs/ with 3 files: one about Python programming, "
         "one about cooking recipes, one about space exploration. "
         "Make each at least 200 words.",
         "msgs>={prev}+2", 120),
        # ── Testing ──
        ("Run: python3 -m {pkg} ingest test_docs/",
         "msgs>={prev}+2", 90),
        ("Run: python3 -m {pkg} list",
         "msgs>={prev}+2", 90),
        ("Query: python3 -m {pkg} query \"How do you make pasta?\"",
         "msgs>={prev}+2", 90),
        ("Query: python3 -m {pkg} query \"What is Python?\" --top-k 3",
         "msgs>={prev}+2", 90),
        ("Query: python3 -m {pkg} query \"Tell me about Mars\"",
         "msgs>={prev}+2", 90),
        # ── Code review ──
        ("Read index.py. Explain the TF-IDF calculation step by step. "
         "Is the IDF formula log(N/df) or log(N/(df+1))?",
         "msgs>={prev}+2", 120),
        ("Read ingest.py. How does the chunking handle the overlap? "
         "Show me the exact chunk boundaries for a 500-word doc "
         "with chunk_size=200 and overlap=50.",
         "msgs>={prev}+2", 90),
        # ── Feature: chunk size ──
        ("The default chunk size is too large. Add a --chunk-size "
         "flag to the ingest command. Use search_replace on cli.py.",
         "msgs>={prev}+4", 120),
        ("Re-ingest with smaller chunks: python3 -m {pkg} ingest "
         "test_docs/ --chunk-size 100",
         "msgs>={prev}+2", 90),
        ("Query again: python3 -m {pkg} query \"How do you make pasta?\" "
         "Are the results more precise now?",
         "msgs>={prev}+2", 90),
        # ── Feature: confidence scores ──
        ("Add confidence scores to the query output. Show the cosine "
         "similarity as a percentage. Use search_replace on query.py.",
         "msgs>={prev}+4", 120),
        ("Test the confidence output with a query.",
         "msgs>={prev}+2", 90),
        # ── Bug hunt ──
        ("What happens if I query before ingesting? Clear the index "
         "and try: python3 -m {pkg} query \"test\"",
         "msgs>={prev}+3", 120),
        ("Fix any crash — it should say 'No index found. Run ingest first.'",
         "msgs>={prev}+3", 120),
        # ── Ideas ──
        ("What would make this system actually useful for real "
         "document search? Give me 5 practical improvements.",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Re-ingest and run 3 different queries. Show full output "
         "with sources and confidence scores.",
         "msgs>={prev}+3", 150),
        ("Update the todo list — show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md with usage examples.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Prompt Optimizer — 24 steps
    # ═══════════════════════════════════════════════════════════════
    "prompt_optimizer": [
        # ── Planning ──
        ("Review the PRD. The default executor uses keyword matching. "
         "Explain how you'll score sentiment with just string matching "
         "and no LLM. Plan the architecture.",
         "msgs>=4", 120),
        ("How will the prompt mutation work? What variations will you "
         "try? Give me 3 example mutations of a base prompt.",
         "msgs>={prev}+2", 90),
        ("Create a detailed todo list. Then start building the template "
         "and mutation module. Execute without stopping.",
         "writes>=2", 150),
        # ── Building ──
        ("Continue the todo list. Build executor, scorer, optimizer.",
         "writes>=5", 150),
        ("Build CLI and __main__.py. Verify --help works.",
         "writes>=7", 120),
        ("Create data.json with 20 sentiment examples — 7 positive, "
         "7 negative, 6 neutral. Include tricky cases like sarcasm.",
         "msgs>={prev}+2", 90),
        # ── Testing ──
        ("Run: python3 -m {pkg} run --task sentiment --dataset data.json "
         "--iterations 3",
         "msgs>={prev}+3", 120),
        ("Show the optimization history: python3 -m {pkg} history --task sentiment",
         "msgs>={prev}+2", 90),
        ("Show the best prompt: python3 -m {pkg} best --task sentiment",
         "msgs>={prev}+2", 90),
        # ── Code review ──
        ("Read executor.py. How does the keyword matching work? Walk "
         "me through scoring a tricky input like 'not bad at all'.",
         "msgs>={prev}+2", 120),
        ("Read optimizer.py. How does it decide which mutations to keep? "
         "Is it greedy or does it explore?",
         "msgs>={prev}+2", 90),
        ("Read templates.py. Show me all the mutation strategies.",
         "msgs>={prev}+2", 90),
        # ── Feature: negation ──
        ("The keyword matching fails on negation. 'Not good' should be "
         "negative but matches 'good'=positive. Fix this in executor.py "
         "using search_replace.",
         "msgs>={prev}+4", 120),
        ("Run the optimizer again with 5 iterations. Is the score better?",
         "msgs>={prev}+3", 120),
        # ── Feature: per-class metrics ──
        ("Add per-class precision to the scorer output. Show accuracy "
         "for positive, negative, and neutral separately. search_replace.",
         "msgs>={prev}+4", 120),
        ("Run and show the per-class breakdown.",
         "msgs>={prev}+3", 120),
        # ── Feature: new task type ──
        ("Add support for a 'topic' classification task. Create "
         "topics.json with 15 examples labeled tech/sports/politics. "
         "The executor should use different keywords per topic.",
         "msgs>={prev}+4", 120),
        ("Run: python3 -m {pkg} run --task topic --dataset topics.json "
         "--iterations 3",
         "msgs>={prev}+3", 120),
        # ── Bug hunt ──
        ("What happens if the dataset has no examples for one class? "
         "Create imbalanced.json with 10 positive and 0 negative. Test.",
         "msgs>={prev}+3", 120),
        ("Fix any division-by-zero or crash.",
         "msgs>={prev}+3", 120),
        # ── Ideas ──
        ("If we had a real LLM instead of keyword matching, how would "
         "the optimization loop change? What about using the LLM to "
         "critique its own prompts?",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Run the full optimization on both sentiment and topic tasks. "
         "Show final scores side by side.",
         "msgs>={prev}+3", 150),
        ("Update the todo list. Show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md explaining the optimization approach.",
         "done", 120),
    ],
}

# Default fallback script for unknown packages
DEFAULT_SCRIPT: list[tuple[str, str, int]] = [
    ("Review the PRD and create a plan. List what files you need.",
     "msgs>=4", 120),
    ("Create a todo list and start building. Execute all items.",
     "writes>=3", 150),
    ("Continue — write the remaining files.",
     "writes>=5", 150),
    ("Test: python3 -m {pkg} --help",
     "msgs>={prev}+2", 90),
    ("Test the main functionality.",
     "msgs>={prev}+3", 120),
    ("Add a new feature using search_replace.",
     "msgs>={prev}+4", 120),
    ("Test the new feature.",
     "msgs>={prev}+2", 90),
    ("What ideas do you have for improvements?",
     "msgs>={prev}+2", 90),
    ("Run all tests one final time and show results.",
     "msgs>={prev}+3", 150),
    ("Write a README.md.",
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
