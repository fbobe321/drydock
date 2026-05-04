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
    """
    try:
        import urllib.request
        prompt = (
            "You are grading a free-form answer against a ground truth. "
            "Respond with exactly one of: YES, NO, or PARTIAL on the first "
            "line, then a one-sentence reason on the second.\n\n"
            f"QUESTION: {question[:1200]}\n"
            f"GROUND TRUTH: {gold[:600]}\n"
            f"PREDICTED ANSWER: {pred[:1200]}\n\n"
            "Verdict:"
        )
        req = urllib.request.Request(
            "http://localhost:8001/v1/chat/completions",
            data=json.dumps({
                "model": "gemma4",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 80,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        text = data["choices"][0]["message"]["content"].strip()
        first = text.splitlines()[0].strip().upper()
        verdict = "ERROR"
        for v in ("YES", "PARTIAL", "NO"):
            if first.startswith(v):
                verdict = v
                break
        return verdict, text[:300]
    except Exception as e:
        return "ERROR", f"judge failed: {e!r}"


def score_answer(q: dict, pred: str) -> dict:
    gold = q["answer"]
    answer_type = q.get("answer_type", "text") or "text"
    if not pred.strip():
        return {"correct": False, "method": "empty", "verdict": "NO",
                "judge_reasoning": "no answer extracted"}
    if answer_type in ("multipleChoice", "exactMatch", "numeric"):
        if exact_score(pred, gold):
            return {"correct": True, "method": "exact", "verdict": "YES"}
        if fuzzy_score(pred, gold):
            return {"correct": True, "method": "fuzzy", "verdict": "YES"}
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

    All HLE runs notify on start, every MILESTONE_EVERY completions, and
    on final completion (or crash). Failures are silent — a missed
    Telegram is never worth crashing the eval.
    """
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


def _extract_answer(messages: list[dict]) -> str:
    """Pull the FINAL ANSWER line out of the last assistant message that
    has text content. Falls back to the full last assistant content if no
    marker is present."""
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        for line in reversed(content.splitlines()):
            ls = line.strip()
            if ls.upper().startswith("FINAL ANSWER:"):
                return ls.split(":", 1)[1].strip()
        return content[-500:].strip()
    return ""


def run_one(q: dict, sk, run_dir: Path) -> dict:
    """Drive drydock TUI on a single HLE question."""
    import pexpect
    cwd = run_dir / "work" / q["id"]
    cwd.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "tui_logs" / f"{q['id']}.tui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, "TERM": "xterm-256color",
           "COLUMNS": "120", "LINES": "30"}
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
    sk.type_message(child, _question_prompt(q))
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
    args = ap.parse_args()

    print(f"[hle_eval] source={args.source} limit={args.limit} "
          f"shuffle={args.shuffle}")

    if args.source == "seed":
        questions = load_seed()
    else:
        questions = load_hle()

    print(f"[hle_eval] loaded {len(questions)} questions")
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
            score = score_answer(q, outcome["predicted"])
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
    notify_telegram(
        "hle-final",
        f"HLE FINAL: {correct}/{n} = {pct:.1f}% "
        f"(SOTA {SOTA_REFERENCE}%, gap {gap:+.1f})\n\n"
        f"By category:\n" + "\n".join(cat_lines)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
