#!/usr/bin/env python3
"""HLE eval — drives the drydock TUI against Humanity's Last Exam questions.

The eval is THIN. drydock IS the harness. We do not wrap the model directly.
Each question is fed as a user prompt to the real drydock TUI; the agent
loop, tools, GraphRAG, and steering hook all run as configured. We just:

  1. load the question
  2. type it into the TUI via pexpect
  3. wait for the TUI to go idle (model says something with no tool call)
  4. capture the final assistant message as the answer
  5. score it (exact-match for numeric/MC, LLM-as-judge for free-form)

Reuses the SessionWatcher + typing utilities from shakedown_interactive.py
so we don't duplicate the bits that have already been hardened on real
sessions (idle detection, trust-dialog handling, prompt-acceptance retry).

Two data sources:

  --source seed   : built-in HLE-shaped seed (7 hand-crafted questions
                    spanning math, physics, chemistry, history, philosophy)
                    used to smoke-test the pipeline without HF auth
  --source hle    : cais/hle from HuggingFace (gated — needs token at
                    ~/.config/drydock/hf_token or HF_TOKEN env)

Output:
  /data3/drydock/hle_results/run_<ts>/
    results.jsonl     # one line per question
    summary.json      # aggregate score + per-category breakdown
    sessions/         # symlinks to drydock session logs for failures

Usage:
  # Smoke-test the pipeline against the seed (no HF auth needed)
  python3 scripts/hle_eval.py --source seed --limit 7

  # Run real HLE (requires token)
  python3 scripts/hle_eval.py --source hle --limit 20 --shuffle

  # Resume an interrupted run from its results.jsonl (skips done IDs)
  python3 scripts/hle_eval.py --source hle --resume /path/to/run_<ts>
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

REPO = Path("/data3/drydock")
SHAKEDOWN = REPO / "scripts" / "shakedown_interactive.py"
NOTIFY = REPO / "scripts" / "notify_release.py"
RESULTS_ROOT = REPO / "hle_results"
MILESTONE_EVERY = 50    # send a Telegram progress ping every N completions
SOTA_REFERENCE = 45.9   # current HLE SOTA per user (2026-05-04); used in pings
SEED_PATH = Path(os.environ.get("HLE_SEED_PATH",
                                str(REPO / "scripts" / "hle_eval_seed.jsonl")))
HF_TOKEN_FILE = Path.home() / ".config" / "drydock" / "hf_token"

# Per-question wall-clock cap. HLE questions can take real reasoning time.
# Coding-style PRDs in shakedown allow 600s for harder steps; QA should be
# faster since there's no file-writing iteration, but Gemma 4 thinking can
# still chew through several minutes on a hard question.
QUESTION_TIMEOUT = 480  # 8 min per question
IDLE_GRACE = 8.0        # seconds of no new messages before declaring done


# ── Reuse shakedown primitives via importlib (no circular imports) ────
def _load_shakedown():
    spec = importlib.util.spec_from_file_location("shakedown_interactive", SHAKEDOWN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Question loaders ──────────────────────────────────────────────────
def _hf_token() -> str | None:
    if HF_TOKEN_FILE.is_file():
        return HF_TOKEN_FILE.read_text().strip()
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def load_seed() -> list[dict]:
    if not SEED_PATH.is_file():
        raise SystemExit(f"seed file not found: {SEED_PATH}")
    out = []
    for line in SEED_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def load_hle() -> list[dict]:
    token = _hf_token()
    if not token:
        raise SystemExit(
            "cais/hle is gated — drop a HuggingFace token (read scope) at\n"
            f"  {HF_TOKEN_FILE}\n"
            "or set HF_TOKEN in env. Request access at "
            "https://huggingface.co/datasets/cais/hle"
        )
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise SystemExit(f"`datasets` not installed: {e}")
    ds = load_dataset("cais/hle", split="test", token=token)
    out = []
    for row in ds:
        # Skip multi-modal questions for now — drydock has no image tool wired.
        # Phase 1 baseline measures text-only HLE; a phase-1.5 image-tool
        # extension can come later.
        if row.get("image"):
            continue
        out.append({
            "id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "answer_type": row.get("answer_type", "text"),
            "category": row.get("category", "?"),
            "raw_subject": row.get("raw_subject", ""),
        })
    return out


# ── Scorer ────────────────────────────────────────────────────────────
def normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def exact_score(pred: str, gold: str) -> bool:
    return normalize(pred) == normalize(gold)


def fuzzy_score(pred: str, gold: str) -> bool:
    """Cheap fallback for short answers: gold appears as substring in pred,
    case/whitespace-normalized. Catches '42' inside 'The answer is 42.' """
    return normalize(gold) in normalize(pred)


def judge_with_gemma(question: str, gold: str, pred: str) -> tuple[str, str]:
    """LLM-as-judge using the same Gemma 4 endpoint drydock uses. Returns
    (verdict, reasoning) with verdict in {YES, NO, PARTIAL, ERROR}.

    Cheap, local, no external API. Validates against the answer drydock
    produced; doesn't use it to drive drydock.

    Robustness notes (2026-05-14):
    - Gemma 4 on hard math judging frequently exhausts the thinking
      budget and returns an empty `content` even when the model
      actually decided a verdict. The old code did
      `text.splitlines()[0]` on an empty string → IndexError → ERROR
      verdict. We saw Q3 Math Q1 produce a mathematically-identical
      answer that got marked ERROR instead of YES because of this.
    - `reasoning_content` (the llama.cpp `--jinja` thinking field)
      sometimes carries the verdict when `content` is empty — we
      search both. Last-resort: scan the full response text for
      YES / NO / PARTIAL tokens.
    - max_tokens bumped 80 → 200 to give a real budget after thinking.
    - On retry-worthy failures (timeout, empty), one extra retry with
      a tighter "answer in one word only" prompt.
    """
    import urllib.request

    def _call(p: str, mt: int) -> str:
        req = urllib.request.Request(
            "http://localhost:8001/v1/chat/completions",
            data=json.dumps({
                "model": "gemma4",
                "messages": [{"role": "user", "content": p}],
                "temperature": 0.0,
                "max_tokens": mt,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        if content:
            return content
        # Fall back to reasoning_content (llama.cpp --jinja thinking field)
        return (msg.get("reasoning_content") or "").strip()

    def _verdict_from(text: str, tail_only: bool = False) -> str:
        if not text:
            return ""
        # When scanning reasoning_content (thinking tokens), the verdict
        # usually appears at the end of the analysis, not the beginning.
        # tail_only=True restricts the scan to the last 300 characters.
        scan = text[-300:] if tail_only else text
        lines = scan.splitlines()
        if lines and not tail_only:
            first = lines[0].strip().upper()
            for v in ("YES", "PARTIAL", "NO"):
                if first.startswith(v):
                    return v
        up = scan.upper()
        for v in ("YES", "PARTIAL", "NO"):
            i = up.find(v)
            if i >= 0:
                end = i + len(v)
                if end == len(up) or not up[end].isalpha():
                    return v
        return ""

    prompt = (
        "You are grading a free-form answer against a ground truth. "
        "Respond with exactly one of: YES, NO, or PARTIAL on the first "
        "line, then a one-sentence reason on the second.\n\n"
        f"QUESTION: {question[:1200]}\n"
        f"GROUND TRUTH: {gold[:600]}\n"
        f"PREDICTED ANSWER: {pred[:1200]}\n\n"
        "Verdict:"
    )
    try:
        text = _call(prompt, 200)
        verdict = _verdict_from(text)
        if not verdict:
            # reasoning_content (thinking tokens) may carry the verdict at
            # the tail of the analysis when content is empty. Scan the last
            # 300 chars with word-boundary check to avoid false positives.
            verdict = _verdict_from(text, tail_only=True)
        if verdict:
            return verdict, text[:300]
        # Tighter retry — bump budget to 256 so thinking + one-word answer
        # both fit (16 was too small: model exhausted the budget on thinking).
        terse = (
            "Grade this answer against the ground truth and reply with "
            "ONE WORD ONLY: YES, NO, or PARTIAL.\n\n"
            f"GROUND TRUTH: {gold[:600]}\nPREDICTED: {pred[:600]}\n\nAnswer:"
        )
        text2 = _call(terse, 256)
        verdict = _verdict_from(text2)
        if not verdict:
            verdict = _verdict_from(text2, tail_only=True)
        if verdict:
            return verdict, f"[retry] {text2[:200]}"
        return "ERROR", f"judge produced no parseable verdict: {(text or text2)[:200]!r}"
    except Exception as e:
        return "ERROR", f"judge failed: {e!r}"


def score_answer(q: dict, pred: str, outcome: dict | None = None) -> dict:
    """Score the model's predicted answer against the ground truth.

    `outcome` is the dict returned by `run_one` (optional). When passed
    and `pred` is empty, we sub-classify the empty failure using
    `msg_count` so the diagnostic distinguishes 'model never started'
    (msg_count<=1) from 'model talked but did not emit FINAL ANSWER:'
    (msg_count>1). This dropped out of the Q4 30-Q overnight diagnosis
    on 2026-05-13 — 26/30 empties shared `method='empty'` but the
    sessions had very different shapes underneath.
    """
    gold = q["answer"]
    answer_type = q.get("answer_type", "text") or "text"
    if not pred.strip():
        method = "empty"
        if outcome is not None:
            msg_count = int(outcome.get("msg_count") or 0)
            if msg_count <= 1:
                # Only the user message landed — the model never even
                # produced a tool call or content token before the
                # harness killed the session.
                method = "empty:no_response"
            else:
                # Assistant did produce messages (tool calls / thinking
                # turns) but extraction found no FINAL ANSWER: line.
                method = "empty:no_final_answer"
        return {"correct": False, "method": method, "verdict": "NO",
                "judge_reasoning": "no answer extracted"}
    if answer_type in ("multipleChoice", "exactMatch", "numeric"):
        if exact_score(pred, gold):
            return {"correct": True, "method": "exact", "verdict": "YES"}
        if fuzzy_score(pred, gold):
            return {"correct": True, "method": "fuzzy", "verdict": "YES"}
        # Short pred (single letter/word) can't embed the right answer —
        # skip the judge to avoid ERROR on an obviously wrong answer.
        if len(normalize(pred)) <= 3:
            return {"correct": False, "method": "exact", "verdict": "NO",
                    "judge_reasoning": "short pred does not match gold"}
        # Fall through to judge for tricky MC where pred has explanation
    verdict, reasoning = judge_with_gemma(q["question"], gold, pred)
    return {
        "correct": verdict == "YES",
        "partial": verdict == "PARTIAL",
        "method": "judge",
        "verdict": verdict,
        "judge_reasoning": reasoning,
    }


# ── Telegram notifications ────────────────────────────────────────────
def notify_telegram(tag: str, message: str) -> None:
    """Fire-and-forget Telegram ping via the existing notify_release.py.

    Default OFF: HLE eval is too noisy for routine notifications and the
    user explicitly asked to stop. Opt in via HLE_TELEGRAM=1 if you want
    start/milestone/final/crash pings for a specific run.

    Failures are silent — a missed Telegram is never worth crashing
    the eval.
    """
    if os.environ.get("HLE_TELEGRAM", "").strip().lower() not in ("1", "true", "yes"):
        return
    if not NOTIFY.exists():
        return
    import subprocess
    try:
        subprocess.run(
            [sys.executable, str(NOTIFY), tag, message],
            timeout=15, capture_output=True,
        )
    except Exception:
        pass


# ── TUI runner per question ───────────────────────────────────────────
def _question_prompt(q: dict) -> str:
    """Wrap the HLE question with a thin instruction so the model knows it's
    answering, not building a project. We do NOT add CoT scaffolding,
    web_search instructions, or anything that would make this 'a custom
    harness' — the only addition is 'state your final answer'.

    Single-line: the drydock TUI's input handler eats internal newlines
    inconsistently when typed char-by-char via pexpect (multi-line paste-
    detection race). Keeping this on one line until that's debugged.
    """
    return (
        f"Answer this question. End your response with the literal string "
        f"'FINAL ANSWER:' followed by your answer on the same line. "
        f"QUESTION: {q['question']}"
    )


def _send_prompt_as_paste(child, text: str) -> None:
    """Send a one-shot prompt to the TUI as a bracketed paste, then Enter.

    Why not `sk.type_message`: it sends char-by-char at 10ms/char, which
    on a ~1500-char HLE prompt takes ~15s and races the Textual input
    handler. In real captured sessions we observed 13 stray `\\n` chars
    injected mid-word (`'Answer th\\n\\n...\\n\\nis question'`) — the
    model then reads garbage and either bails or grinds in tool-call
    loops without ever emitting `FINAL ANSWER:`. Bracketed paste
    (xterm convention also recognised by Textual >=0.40) tells the
    receiving widget "this is a single literal block — do not interpret
    embedded chars as keypresses or trigger paste-detection logic."

    Sequence:
      ESC [ 2 0 0 ~  <text-with-newlines-stripped>  ESC [ 2 0 1 ~  <Enter>

    Newlines inside the prompt are stripped because Textual's Input
    widget commits on Enter regardless of paste markers. HLE prompts
    are a single line by construction (`_question_prompt`), so this is
    a no-op for the supported case.
    """
    safe_text = text.replace("\r", " ").replace("\n", " ")
    PASTE_START = "\x1b[200~"
    PASTE_END = "\x1b[201~"
    child.send(PASTE_START + safe_text + PASTE_END)
    time.sleep(0.3)  # let the widget commit the paste
    child.send("\r")


def _extract_answer(messages: list[dict]) -> str:
    """Pull the FINAL ANSWER value out of the last assistant message
    that has text content. Falls back to the tail of the last assistant
    content if no marker is present.

    Robustness extras (2026-05-14):
    - tolerate `content` being a list of {'type':'text','text':...} parts
      (Anthropic-style multipart) — flatten before searching
    - strip markdown emphasis (`**FINAL ANSWER:** x` → `x`)
    - unwrap a single trailing `\\boxed{...}` wrapper (common math convention)
    - normalize surrounding `$...$` math delimiters
    """
    import re as _re

    def _flat(c) -> str:
        if c is None:
            return ""
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = []
            for item in c:
                if isinstance(item, dict):
                    t = item.get("text") or item.get("content")
                    if isinstance(t, str):
                        parts.append(t)
            return "\n".join(parts)
        return str(c)

    _MARKER_RE = _re.compile(
        r"^\**\s*(?:FINAL\s+ANSWER|ANSWER)\s*:\s*\**\s*",
        _re.IGNORECASE,
    )
    _BOXED_RE = _re.compile(r"\\boxed\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
    _MATH_DOLLAR_RE = _re.compile(r"^\$+(.*?)\$+$")

    def _cleanup(v: str) -> str:
        v = v.strip()
        # Unwrap \boxed{x} → x (only if it's the whole answer).
        m = _BOXED_RE.fullmatch(v)
        if m:
            v = m.group(1).strip()
        # Strip surrounding $...$ delimiters when they wrap the whole value.
        m = _MATH_DOLLAR_RE.fullmatch(v)
        if m:
            v = m.group(1).strip()
        return v

    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        content = _flat(m.get("content")).strip()
        if not content:
            continue
        for line in reversed(content.splitlines()):
            ls = line.strip()
            marker = _MARKER_RE.match(ls)
            if marker:
                return _cleanup(ls[marker.end():])
        # No marker — take the tail. Strip trailing newlines / markdown
        # fences (model sometimes wraps the answer in a code block).
        tail = content[-500:].strip()
        for fence in ("```", "$$"):
            if tail.endswith(fence):
                tail = tail.rsplit(fence, 1)[0].rstrip()
        return _cleanup(tail)
    return ""


def run_one(q: dict, sk, run_dir: Path) -> dict:
    """Drive drydock TUI on a single HLE question."""
    import pexpect
    cwd = run_dir / "work" / q["id"]
    cwd.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "tui_logs" / f"{q['id']}.tui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, "TERM": "xterm-256color",
           "COLUMNS": "120", "LINES": "30",
           # HLE sessions must commit quickly. Lower turn thresholds so the
           # model gets the wrap-up warning before the 480s wall-clock kills it.
           # Default 30/60 is for coding tasks; 8/12 forces an answer sooner.
           # On engineering/math questions llama.cpp Q3 averages 60-160s/turn,
           # so the turn-based STOP_NOW at 12 fires at 720-1920s — well past
           # the 480s wall clock. STOP_NOW_TIME_SEC=300 fires at 5 minutes
           # regardless of turn count, giving the model 3 minutes to respond.
           # Respect env overrides (e.g. babysitter exports lower values).
           "DRYDOCK_WRAP_UP_WARN_AT": os.environ.get("DRYDOCK_WRAP_UP_WARN_AT", "8"),
           "DRYDOCK_STOP_NOW_WARN_AT": os.environ.get("DRYDOCK_STOP_NOW_WARN_AT", "12"),
           "DRYDOCK_STOP_NOW_TIME_SEC": os.environ.get("DRYDOCK_STOP_NOW_TIME_SEC", "300"),
           "DRYDOCK_STOP_NOW_SUFFIX": "Write your best answer as 'FINAL ANSWER: <answer>' now."}
    start = time.time()
    # --dangerously-skip-permissions: HLE is batch eval against read-only
    # tools (web_search, retrieve, grep, read_file). Without it, web_search
    # blocks waiting for an approval prompt the harness can't see and the
    # session stalls until our timeout fires. Still real TUI, still real
    # agent loop — just bypasses per-tool permission gating.
    child = pexpect.spawn(sk.DRYDOCK_BIN,
                          args=["--dangerously-skip-permissions"],
                          encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "w")

    try:
        child.expect([r">", r"Drydock", r"┌"], timeout=30)
    except Exception:
        pass
    time.sleep(2)

    # Trust-dialog dismiss (every fresh cwd triggers it)
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.2)
            child.send("\r")
            time.sleep(2)
    except Exception:
        pass

    watcher = sk.SessionWatcher(cwd, since=start)
    _send_prompt_as_paste(child, _question_prompt(q))
    time.sleep(1)

    # Wait for session dir to appear
    for _ in range(60):
        sk.drain_pty(child)
        if watcher.find_session():
            break
        time.sleep(1)

    deadline = start + QUESTION_TIMEOUT
    last_msg_count = 0
    last_change = time.time()
    while time.time() < deadline:
        sk.drain_pty(child)
        if not child.isalive():
            break
        watcher.refresh()
        n = len(watcher.messages)
        if n != last_msg_count:
            last_msg_count = n
            last_change = time.time()
        # Idle-with-final-text-only = done
        if watcher.model_said_done() and (time.time() - last_change) > IDLE_GRACE:
            break
        time.sleep(2)

    elapsed = time.time() - start
    pred = _extract_answer(watcher.messages)
    session_dir = str(watcher.session_dir) if watcher.session_dir else ""

    try:
        child.sendcontrol("c")
        child.terminate(force=True)
    except Exception:
        pass

    return {
        "id": q["id"],
        "category": q.get("category", "?"),
        "answer_type": q.get("answer_type", "text"),
        "elapsed_s": round(elapsed, 1),
        "msg_count": last_msg_count,
        "predicted": pred,
        "ground_truth": q["answer"],
        "session_dir": session_dir,
    }


# ── Orchestrator ──────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("seed", "hle"), default="seed")
    ap.add_argument("--limit", type=int, default=7)
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--resume", type=Path,
                    help="resume from existing run dir (skip already-done IDs)")
    ap.add_argument(
        "--category", default="",
        help=(
            "Filter to questions in this HLE category (case-insensitive "
            "substring match against q['category']). Examples: 'math', "
            "'chemistry', 'physics'. Use this to measure tool-impact on a "
            "specific axis."
        ),
    )
    args = ap.parse_args()

    print(f"[hle_eval] source={args.source} limit={args.limit} "
          f"shuffle={args.shuffle} category={args.category or '(any)'}")

    if args.source == "seed":
        questions = load_seed()
    else:
        questions = load_hle()

    print(f"[hle_eval] loaded {len(questions)} questions")

    if args.category:
        cat = args.category.lower().strip()
        before = len(questions)
        questions = [q for q in questions if cat in (q.get("category", "") or "").lower()]
        print(f"[hle_eval] category filter '{args.category}': {before} → {len(questions)} questions")

    if args.shuffle:
        random.Random(args.seed).shuffle(questions)
    questions = questions[: args.limit]

    if args.resume and args.resume.is_dir():
        run_dir = args.resume
        done_ids = set()
        rfile = run_dir / "results.jsonl"
        if rfile.exists():
            for ln in rfile.read_text().splitlines():
                try:
                    done_ids.add(json.loads(ln)["id"])
                except Exception:
                    pass
        questions = [q for q in questions if q["id"] not in done_ids]
        print(f"[hle_eval] resuming run {run_dir.name}, {len(done_ids)} done, "
              f"{len(questions)} remaining")
    else:
        ts = int(time.time())
        run_dir = RESULTS_ROOT / f"run_{ts}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "config.json").write_text(json.dumps({
            "source": args.source,
            "limit": args.limit,
            "shuffle": args.shuffle,
            "seed": args.seed,
            "started_at": ts,
        }, indent=2))

    sk = _load_shakedown()
    results_file = run_dir / "results.jsonl"

    # Pre-count what's already in results.jsonl (for --resume)
    prior_done = 0
    prior_correct = 0
    if results_file.exists():
        for ln in results_file.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
                prior_done += 1
                if r.get("correct"):
                    prior_correct += 1
            except Exception:
                pass

    notify_telegram(
        "hle-start",
        f"HLE eval started — {args.source} N={args.limit} "
        f"(resuming with {prior_done} done, {prior_correct} correct). "
        f"Will ping every {MILESTONE_EVERY} completions and at final. "
        f"SOTA reference: {SOTA_REFERENCE}%."
    )

    completed = prior_done
    running_correct = prior_correct
    last_milestone = (prior_done // MILESTONE_EVERY) * MILESTONE_EVERY

    try:
        for i, q in enumerate(questions, 1):
            print(f"\n[{i}/{len(questions)}] {q['id']}  ({q.get('category','?')})")
            print(f"  Q: {q['question'][:120]}")
            try:
                outcome = run_one(q, sk, run_dir)
            except Exception as e:
                outcome = {
                    "id": q["id"], "category": q.get("category", "?"),
                    "answer_type": q.get("answer_type", "text"),
                    "elapsed_s": 0.0, "msg_count": 0,
                    "predicted": "", "ground_truth": q["answer"],
                    "session_dir": "", "runner_error": repr(e),
                }
            score = score_answer(q, outcome["predicted"], outcome)
            outcome.update(score)
            print(f"  pred: {outcome['predicted'][:120]}")
            print(f"  gold: {q['answer'][:120]}")
            print(f"  → {outcome['verdict']:8s} ({outcome['method']}, "
                  f"{outcome['elapsed_s']:.0f}s, {outcome['msg_count']} msgs)")
            with results_file.open("a") as f:
                f.write(json.dumps(outcome) + "\n")
            completed += 1
            if outcome.get("correct"):
                running_correct += 1
            else:
                # Curiosity feedback loop (SOVEREIGN_PRD §5.7 acceptance #4):
                # every HLE failure becomes a learning signal for the next
                # autonomous_review tick. We care most about "empty" failures
                # (model produced no answer at all — typically a retrieve
                # gap) and judge-marked NO with explicit reasoning.
                try:
                    sys.path.insert(0, str(REPO))
                    from drydock.curiosity import (
                        CuriosityItem, CuriosityKind, enqueue,
                    )
                    method = outcome.get("method", "")
                    judge_r = outcome.get("judge_reasoning", "")
                    kind = CuriosityKind.HLE_FAILURE
                    qid = outcome.get("id", "?")
                    enqueue(CuriosityItem(
                        kind=kind,
                        term=q.get("question", "")[:200],
                        context=(
                            f"Predicted: {outcome.get('predicted', '')[:200]}\n"
                            f"Gold: {q.get('answer', '')[:200]}\n"
                            f"Judge: {judge_r[:200]}"
                        ),
                        source=f"hle:{qid}",
                        suggested_action=(
                            # Refined per Q4 30-Q diagnosis (2026-05-13):
                            # `empty:no_response` (msg_count<=1) is a
                            # thinking-stall — model never started, so
                            # the action is harness-side (timeout,
                            # forcing function, quant). `empty:no_final_answer`
                            # (msg_count>1) means the model engaged but
                            # didn't produce a FINAL ANSWER: line — that's
                            # a retrieval / prompt-rule gap.
                            "Model never produced a response within the "
                            "session timeout. Action: investigate thinking-"
                            "budget exhaustion (raise timeout, force tool-"
                            "call cap, consider Q3 vs Q4 quant)."
                            if method == "empty:no_response"
                            else "Model engaged with tools but never emitted "
                                 "FINAL ANSWER: — investigate retrieval "
                                 "coverage / prompt rule to force "
                                 "answer-after-N-turns."
                            if method == "empty:no_final_answer"
                            else "Investigate retrieval coverage for this "
                                 "topic; consider GraphRAG ingest of relevant "
                                 "corpus or a prompt rule to force retrieve "
                                 "before answering."
                            if method == "empty"
                            else "Compare predicted vs gold; surface to "
                                 "autonomous_review as a prompt/AGENTS.md "
                                 "candidate."
                        ),
                        confidence=(
                            0.9 if method.startswith("empty") else 0.6
                        ),
                        extra={"category": q.get("category", ""),
                               "method": method},
                    ))
                except Exception:
                    # Curiosity is best-effort; never let it interrupt eval.
                    pass
            # Milestone ping every MILESTONE_EVERY completions
            if completed >= last_milestone + MILESTONE_EVERY:
                last_milestone = (completed // MILESTONE_EVERY) * MILESTONE_EVERY
                pct = (running_correct / completed * 100) if completed else 0
                gap = pct - SOTA_REFERENCE
                notify_telegram(
                    "hle-progress",
                    f"HLE progress {completed}/{args.limit}: "
                    f"{running_correct}/{completed} = {pct:.1f}% "
                    f"(SOTA {SOTA_REFERENCE}%, gap {gap:+.1f})"
                )
    except Exception as e:
        notify_telegram(
            "hle-crash",
            f"HLE run crashed at {completed}/{args.limit} "
            f"({running_correct}/{completed} so far). Error: {e!r}. "
            f"Resume with --resume {run_dir.name}"
        )
        raise

    # Aggregate
    n = 0
    correct = 0
    by_cat: dict = {}
    for ln in results_file.read_text().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        n += 1
        if r.get("correct"):
            correct += 1
        cat = r.get("category", "?")
        by_cat.setdefault(cat, [0, 0])
        by_cat[cat][1] += 1
        if r.get("correct"):
            by_cat[cat][0] += 1

    summary = {
        "total": n,
        "correct": correct,
        "score": round(correct / n, 4) if n else 0.0,
        "by_category": {c: {"correct": v[0], "total": v[1],
                            "score": round(v[0] / v[1], 4) if v[1] else 0.0}
                        for c, v in sorted(by_cat.items())},
        "run_dir": str(run_dir),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n{'='*60}")
    print(f"  HLE BASELINE: {correct}/{n} = {summary['score']*100:.1f}%")
    print(f"{'='*60}")
    cat_lines = []
    for cat, v in summary["by_category"].items():
        line = f"  {cat:30s} {v['correct']}/{v['total']}  ({v['score']*100:.0f}%)"
        print(line)
        cat_lines.append(line.strip())
    print(f"\n  Results: {run_dir}")

    pct = summary["score"] * 100
    gap = pct - SOTA_REFERENCE

    # Avoid spamming telegram on every 10-Q babysitter batch — most
    # land at 0% under the current floor. Only ping when the batch is
    # genuinely interesting:
    #   - any correct answers AND score >= NOTABLE_PCT (default 10%)
    #   - OR an outright crash / no-result case (caller path)
    #   - OR the batch hit ≥ correctness watermark observed in last 24h
    # Operator override: HLE_EVAL_NOTIFY=always sends every batch
    # (legacy behaviour); HLE_EVAL_NOTIFY=never silences fully.
    notify_mode = os.environ.get("HLE_EVAL_NOTIFY", "auto").lower()
    NOTABLE_PCT = float(os.environ.get("HLE_EVAL_NOTABLE_PCT", "10.0"))
    should_notify = False
    if notify_mode == "always":
        should_notify = True
    elif notify_mode == "never":
        should_notify = False
    else:
        # auto: only notable scores
        should_notify = correct > 0 and pct >= NOTABLE_PCT

    if should_notify:
        notify_telegram(
            "hle-final",
            f"HLE FINAL: {correct}/{n} = {pct:.1f}% "
            f"(SOTA {SOTA_REFERENCE}%, gap {gap:+.1f})\n\n"
            f"By category:\n" + "\n".join(cat_lines)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
