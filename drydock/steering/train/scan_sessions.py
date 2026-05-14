"""Content-based derailment scanner — bypass admiral, find pairs directly.

Why this exists: admiral_state.json persists across the lifetime of
the install, but the session logs it references get rotated off disk
over time. As of 2026-05-14 only 10 of admiral's flagged sessions are
still resolvable — far too thin to train a Deep Noir vector. The
12,424 sessions currently on disk DO contain plenty of the same
derailment shapes; we just need to find them from message content
directly.

Supported patterns:

- `empty_after_tool[:<tool_name>]` — an assistant turn with empty
  content AND empty tool_calls immediately after a tool result. If
  `:<tool_name>` is specified, only matches when the prior tool call
  was that name.
- `loop:<tool_name>` — three or more consecutive assistant turns
  calling the same `<tool_name>` with byte-identical arguments.
- `retry_after_error:<tool_name>` — assistant retries a tool call
  whose previous result contained an error keyword (Error, Traceback,
  FAILED).

Output mirrors `extract_pairs.py`: JSONL with `{id, label, prompt,
completion}`. Pairs feed directly into `drydock.steering.train.capture`.

CLI:

    python -m drydock.steering.train.scan_sessions \\
        --sessions-dir ~/.drydock/logs/session \\
        --sessions-dir ~/.vibe/logs/session \\
        --pattern empty_after_tool:bash \\
        --out pairs.jsonl \\
        --max-derailed 200 \\
        --max-good 200
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("drydock.steering.train.scan_sessions")


# ---- Pattern detectors -----------------------------------------------------

def _detect_empty_after_tool(
    msgs: list[dict[str, Any]], tool_filter: Optional[str]
) -> bool:
    """True if the session contains an empty assistant turn (no content
    AND no tool_calls) right after a tool result. With `tool_filter`,
    only counts the case where the preceding tool was that named tool.
    """
    for i in range(len(msgs) - 1):
        if msgs[i].get("role") != "tool":
            continue
        if tool_filter and msgs[i].get("name") != tool_filter:
            continue
        nxt = msgs[i + 1]
        if nxt.get("role") != "assistant":
            continue
        content = nxt.get("content")
        if isinstance(content, str) and content.strip():
            continue
        if isinstance(content, list) and any(
            isinstance(c, dict) and (c.get("text") or "").strip()
            for c in content
        ):
            continue
        if nxt.get("tool_calls"):
            continue
        return True
    return False


def _detect_loop(msgs: list[dict[str, Any]], tool_name: str) -> bool:
    """True if `tool_name` was called 3+ times in a row with identical args."""
    streak = 0
    last_sig: Optional[str] = None
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        tcs = m.get("tool_calls") or []
        for tc in tcs:
            fn = tc.get("function") or {}
            if fn.get("name") != tool_name:
                streak = 0
                last_sig = None
                continue
            sig = fn.get("arguments") or ""
            if sig == last_sig:
                streak += 1
                if streak >= 2:  # 1 + 2 = 3 identical
                    return True
            else:
                streak = 0
                last_sig = sig
    return False


_ERROR_TOKENS = ("Error", "error", "Traceback", "FAILED", "exit code 1")


def _detect_retry_after_error(
    msgs: list[dict[str, Any]], tool_name: str
) -> bool:
    """True if any tool returned an error and the next assistant turn
    immediately retried the same tool."""
    for i in range(len(msgs) - 1):
        m = msgs[i]
        if m.get("role") != "tool":
            continue
        if m.get("name") != tool_name:
            continue
        content = m.get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        if not any(tok in text for tok in _ERROR_TOKENS):
            continue
        nxt = msgs[i + 1]
        if nxt.get("role") != "assistant":
            continue
        for tc in nxt.get("tool_calls") or []:
            if (tc.get("function") or {}).get("name") == tool_name:
                return True
    return False


def _detect(pattern: str, msgs: list[dict[str, Any]]) -> bool:
    """Dispatch the right detector for `pattern`."""
    if pattern.startswith("empty_after_tool"):
        _, _, t = pattern.partition(":")
        return _detect_empty_after_tool(msgs, t or None)
    if pattern.startswith("loop:"):
        _, _, t = pattern.partition(":")
        if not t:
            return False
        return _detect_loop(msgs, t)
    if pattern.startswith("retry_after_error:"):
        _, _, t = pattern.partition(":")
        if not t:
            return False
        return _detect_retry_after_error(msgs, t)
    raise ValueError(f"unknown pattern: {pattern!r}")


# ---- Pair extraction (mirrors extract_pairs._last_user_assistant_pair) ----

def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in (None, "text"):
                t = item.get("text") or item.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(p.strip() for p in parts if p.strip())
    return ""


def _last_user_assistant_pair(
    msgs: list[dict[str, Any]],
) -> Optional[tuple[str, str]]:
    last_user: Optional[str] = None
    matched: Optional[tuple[str, str]] = None
    for m in msgs:
        role = m.get("role")
        if role == "user":
            t = _extract_text(m.get("content"))
            if t:
                last_user = t
        elif role == "assistant":
            t = _extract_text(m.get("content"))
            if t and last_user is not None:
                matched = (last_user, t)
    return matched


# ---- Session iteration -----------------------------------------------------

def _iter_session_dirs(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and (child / "messages.jsonl").is_file():
                yield child


def _read_messages(d: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text = (d / "messages.jsonl").read_text(errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _session_id_from_meta(d: Path) -> str:
    f = d / "meta.json"
    if not f.is_file():
        return d.name
    try:
        return json.loads(f.read_text()).get("session_id") or d.name
    except json.JSONDecodeError:
        return d.name


# ---- CLI ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="drydock.steering.train.scan_sessions",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--sessions-dir", required=True, action="append", type=Path,
        help="Session root (repeatable).",
    )
    ap.add_argument("--pattern", required=True,
                    help="Pattern name, e.g. empty_after_tool:bash")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--max-derailed", type=int, default=200)
    ap.add_argument("--max-good", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-chars", type=int, default=20,
                    help="Drop pairs whose user or assistant text is shorter.")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = ap.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    rng = random.Random(args.seed)

    derailed: list[dict[str, Any]] = []
    good_candidates: list[Path] = []
    scanned = 0
    for d in _iter_session_dirs(args.sessions_dir):
        scanned += 1
        try:
            msgs = _read_messages(d)
        except OSError:
            continue
        if not msgs:
            continue
        try:
            is_derailed = _detect(args.pattern, msgs)
        except Exception as e:
            logger.debug("detect failed on %s: %s", d.name, e)
            continue
        if is_derailed:
            pair = _last_user_assistant_pair(msgs)
            if pair and len(pair[0]) >= args.min_chars and len(pair[1]) >= args.min_chars:
                derailed.append({
                    "id": f"{_session_id_from_meta(d)}:derailed",
                    "label": "derailed",
                    "prompt": pair[0],
                    "completion": pair[1],
                })
                if len(derailed) >= args.max_derailed:
                    break
        else:
            good_candidates.append(d)

    logger.info("derailed: kept=%d (scanned=%d)", len(derailed), scanned)

    rng.shuffle(good_candidates)
    good: list[dict[str, Any]] = []
    for d in good_candidates:
        if len(good) >= args.max_good:
            break
        msgs = _read_messages(d)
        pair = _last_user_assistant_pair(msgs)
        if not pair:
            continue
        if len(pair[0]) < args.min_chars or len(pair[1]) < args.min_chars:
            continue
        good.append({
            "id": f"{_session_id_from_meta(d)}:good",
            "label": "good",
            "prompt": pair[0],
            "completion": pair[1],
        })
    logger.info("good: kept=%d (candidates=%d)", len(good), len(good_candidates))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for rec in derailed + good:
            f.write(json.dumps(rec) + "\n")
    logger.info("wrote %d pairs to %s (%d derailed + %d good)",
                len(derailed) + len(good), args.out, len(derailed), len(good))
    return 0


if __name__ == "__main__":
    sys.exit(main())
