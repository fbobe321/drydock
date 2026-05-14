"""Extract (good, derailed) contrastive pairs from admiral_history.

Bridges real drydock session data into the M3 capture pipeline.
Per DEEP_NOIR_PRD.md "Decisions (locked 2026-05-09)":

> Pair source: admiral_history only for v1. ~3 weeks of stress +
> autonomous_review traces is the contrastive set. No synthetic
> pair generation in this PRD.

Workflow:

    admiral_state.json (findings → session_ids index)
        +
    ~/.drydock/logs/session/<id>/messages.jsonl
    ~/.vibe/logs/session/<id>/messages.jsonl
        │
        │  python -m drydock.steering.train.extract_pairs \\
        │      --admiral-state ~/.drydock/admiral_state.json \\
        │      --sessions-dir ~/.drydock/logs/session \\
        │      --sessions-dir ~/.vibe/logs/session \\
        │      --finding empty_after_tool:bash \\
        │      --out pairs.jsonl
        ▼
    pairs.jsonl  (ready for `drydock.steering.train.capture`)

Pair definitions for v1:

- "derailed": each session listed under the chosen finding contributes
  one pair. We extract the LAST (user_text → assistant_text) message
  pair in the session — that's the most recent exchange before
  Admiral flagged the failure pattern. Sessions without any usable
  text-content assistant message are skipped.
- "good": sampled from sessions that DON'T appear in ANY finding
  (i.e. ran clean). Same extraction (last text→text exchange).

This v1 is intentionally crude — one pair per session, simple
text→text — because the M4 vector training is itself v1 ("any
non-zero direction works"). M5 calibrates which extraction strategy
matters; right now we just need a real pair set to point M3 at.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("drydock.steering.train.extract_pairs")


def _load_admiral_findings(path: Path) -> dict[str, list[str]]:
    """Return `{finding_name: [session_id, ...]}` from admiral_state.json.

    Tolerates two known shapes:
      - findings[name] = {"sessions": [...], "last_seen": ...}     (current)
      - findings[name] = [...]                                      (legacy)
    """
    state = json.loads(path.read_text())
    raw = state.get("findings", {})
    out: dict[str, list[str]] = {}
    for name, payload in raw.items():
        if isinstance(payload, dict) and "sessions" in payload:
            out[name] = list(payload["sessions"])
        elif isinstance(payload, list):
            out[name] = list(payload)
        else:
            logger.warning(
                "skipping finding %r: unsupported shape %s", name, type(payload).__name__
            )
    return out


def _all_flagged_session_ids(findings: dict[str, list[str]]) -> set[str]:
    flagged: set[str] = set()
    for sids in findings.values():
        flagged.update(sids)
    return flagged


_META_MAP_CACHE: dict[tuple[str, ...], dict[str, Path]] = {}


def _build_meta_map(roots: list[Path]) -> dict[str, Path]:
    """Walk every session dir under `roots` and build a
    `{meta.session_id → dir}` lookup. Cached per `roots` tuple so
    repeated calls within one extraction don't re-crawl.

    Why this exists: admiral records each finding's `session_id` as the
    full UUID written by drydock to `meta.json` (e.g.
    `5b55aacd-3606-4656-ad4b-eacf86506eda`). The session DIRECTORY,
    however, is named `session_<date>_<time>_<short-hash>` where the
    `<short-hash>` is the first 8 chars of the dir's OWN id at create
    time. That id is unrelated to the admiral-recorded UUID — same
    process, but admiral generates independently. The original
    `endswith(short_admiral_uuid)` match therefore almost never hit;
    the May 2026 extraction yielded 2 / 88 sessions for the dominant
    `empty_after_tool:bash` finding because of this mismatch.

    The fix: read every dir's `meta.json`, key by its `session_id`
    field, and look admiral's UUID up directly. One sequential walk
    per (roots, run) is cheap relative to the rest of the pipeline.
    """
    key = tuple(str(r) for r in roots)
    cached = _META_MAP_CACHE.get(key)
    if cached is not None:
        return cached

    mapping: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            meta_file = child / "meta.json"
            if not meta_file.is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            sid = meta.get("session_id")
            if isinstance(sid, str) and sid:
                # Last-writer-wins is fine; sessions don't share UUIDs.
                mapping[sid.lower()] = child
    _META_MAP_CACHE[key] = mapping
    logger.info(
        "meta-map: indexed %d session dirs across %d root(s)",
        len(mapping), len(roots),
    )
    return mapping


def _find_session_dir(session_id: str, roots: list[Path]) -> Optional[Path]:
    """Resolve a session_id to its directory across multiple roots.

    Three resolution strategies in order of fidelity:
      1. Direct: `<root>/<session_id>/` (legacy UUID-named dirs).
      2. meta.json crawl: build a `{meta.session_id → dir}` map and
         look up the full admiral UUID. This is the path that works
         for current session-dir naming (`session_<date>_<time>_<short>`
         where short ≠ admiral's UUID).
      3. Fallback: name-suffix match on the first 8 hex chars of the
         id. Almost never hits in practice; kept for tests that
         construct dir names directly from the id.
    """
    if not session_id:
        return None
    sid_lc = session_id.lower()

    for root in roots:
        if not root.is_dir():
            continue
        # 1. Direct match: <root>/<session_id>/
        direct = root / session_id
        if direct.is_dir():
            return direct

    # 2. meta.json map (the load-bearing path on real session data).
    mapping = _build_meta_map(roots)
    hit = mapping.get(sid_lc)
    if hit is not None:
        return hit

    # 3. Legacy fallback: name-suffix match.
    short = session_id.replace("-", "")[:8].lower()
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and child.name.lower().endswith(short):
                return child
    return None


def _read_messages(session_dir: Path) -> list[dict[str, Any]]:
    f = session_dir / "messages.jsonl"
    if not f.is_file():
        return []
    msgs: list[dict[str, Any]] = []
    for line in f.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return msgs


def _last_user_assistant_pair(
    msgs: list[dict[str, Any]],
) -> Optional[tuple[str, str]]:
    """Return the LAST (user_text, assistant_text) pair where the
    assistant message has non-empty text content. Returns None if no
    usable pair exists.

    Walks the messages in order, tracking the most recent user text;
    when an assistant message with non-empty text content arrives,
    pairs them. Subsequent matches overwrite, so the result is the
    LAST matched pair (closest to the failure or to session end).
    """
    last_user: Optional[str] = None
    matched: Optional[tuple[str, str]] = None
    for m in msgs:
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            text = _extract_text(content)
            if text:
                last_user = text
        elif role == "assistant":
            text = _extract_text(content)
            if text and last_user is not None:
                matched = (last_user, text)
    return matched


def _extract_text(content: Any) -> str:
    """Pull plain text from a message's `content` field.

    The TUI persists either a string OR a list of {'type': 'text',
    'text': '...'} parts (for tool-use cases). We normalize to a
    flat string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in (None, "text"):
                t = item.get("text") or item.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(p.strip() for p in parts if p.strip())
    return ""


def _make_pair_record(
    session_id: str, user_text: str, assistant_text: str, label: str
) -> dict[str, Any]:
    return {
        "id": f"{session_id}:{label}",
        "label": label,
        "prompt": user_text,
        "completion": assistant_text,
    }


def _resolve_flagged_dir_names(
    findings: dict[str, list[str]], sessions_dirs: list[Path]
) -> set[str]:
    """Resolve every flagged session_id to its on-disk directory name.

    Sessions are sometimes stored under `session_<ts>_<short-uuid>`
    rather than the full UUID, so a sid like `5b55aacd-...-...` may
    map to a dir named `session_20260504_5b55aacd`. The clean-set
    iteration walks directory names — so to keep flagged sessions
    out of the control group we must compare on the resolved dir
    name, not the raw sid.
    """
    out: set[str] = set()
    for sids in findings.values():
        for sid in sids:
            out.add(sid)
            sdir = _find_session_dir(sid, sessions_dirs)
            if sdir is not None:
                out.add(sdir.name)
    return out


def _iter_clean_session_ids(
    sessions_dirs: list[Path], flagged_dir_names: set[str], limit: int
) -> Iterable[str]:
    """Yield session directory names whose dir exists and which aren't
    in any flagged set (compared by resolved dir name). Order:
    deterministic by name; caller samples downstream.
    """
    seen: set[str] = set()
    for root in sessions_dirs:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            sid = child.name
            if sid in flagged_dir_names or sid in seen:
                continue
            seen.add(sid)
            yield sid
            if len(seen) >= limit:
                return


def extract_pairs(
    *,
    admiral_state: Path,
    sessions_dirs: list[Path],
    finding: str,
    max_derailed: int = 200,
    max_good: int = 200,
    seed: int = 0,
    min_chars: int = 32,
) -> list[dict[str, Any]]:
    """Build the contrastive pair set in memory. Pure function — does
    not write to disk; the CLI handles serialisation.
    """
    findings = _load_admiral_findings(admiral_state)
    if finding not in findings:
        avail = sorted(findings.keys())[:20]
        raise SystemExit(
            f"finding {finding!r} not in admiral_state. "
            f"Available examples: {avail!r}"
        )
    derailed_ids = list(findings[finding])
    flagged_dir_names = _resolve_flagged_dir_names(findings, sessions_dirs)

    rng = random.Random(seed)
    rng.shuffle(derailed_ids)

    pairs: list[dict[str, Any]] = []
    derailed_kept = 0
    derailed_missing = 0
    for sid in derailed_ids:
        if derailed_kept >= max_derailed:
            break
        sdir = _find_session_dir(sid, sessions_dirs)
        if sdir is None:
            derailed_missing += 1
            continue
        msgs = _read_messages(sdir)
        pair = _last_user_assistant_pair(msgs)
        if pair is None:
            continue
        prompt, completion = pair
        if len(prompt) + len(completion) < min_chars:
            continue
        pairs.append(_make_pair_record(sid, prompt, completion, "derailed"))
        derailed_kept += 1
    logger.info(
        "derailed: kept=%d missing-dir=%d (target=%d, available=%d)",
        derailed_kept, derailed_missing, max_derailed, len(derailed_ids),
    )

    # Pull a wider net of clean sessions then random-sample so the
    # control set isn't biased by directory ordering.
    candidates = list(_iter_clean_session_ids(
        sessions_dirs, flagged_dir_names, limit=max_good * 5
    ))
    rng.shuffle(candidates)
    good_kept = 0
    for sid in candidates:
        if good_kept >= max_good:
            break
        sdir = _find_session_dir(sid, sessions_dirs)
        if sdir is None:
            continue
        msgs = _read_messages(sdir)
        pair = _last_user_assistant_pair(msgs)
        if pair is None:
            continue
        prompt, completion = pair
        if len(prompt) + len(completion) < min_chars:
            continue
        pairs.append(_make_pair_record(sid, prompt, completion, "good"))
        good_kept += 1
    logger.info(
        "good: kept=%d (target=%d, candidates_scanned=%d)",
        good_kept, max_good, len(candidates),
    )
    return pairs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="drydock.steering.train.extract_pairs",
        description=__doc__.split("\n")[0],
    )
    ap.add_argument(
        "--admiral-state", required=True, type=Path,
        help="Path to admiral_state.json",
    )
    ap.add_argument(
        "--sessions-dir", required=True, type=Path, action="append",
        help="Session log root (repeatable, e.g. once for ~/.drydock and "
             "once for ~/.vibe)",
    )
    ap.add_argument(
        "--finding", required=True,
        help="Admiral finding name to use as the 'derailed' label "
             "(e.g. empty_after_tool:bash)",
    )
    ap.add_argument("--out", required=True, type=Path, help="Output pairs.jsonl")
    ap.add_argument("--max-derailed", type=int, default=200)
    ap.add_argument("--max-good", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--min-chars", type=int, default=32,
        help="Skip pairs whose prompt+completion is shorter than this "
             "(empty/trivial pairs aren't useful contrastive data)",
    )
    ap.add_argument(
        "--log-level", default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.admiral_state.is_file():
        raise SystemExit(f"--admiral-state not found: {args.admiral_state}")

    pairs = extract_pairs(
        admiral_state=args.admiral_state,
        sessions_dirs=list(args.sessions_dir),
        finding=args.finding,
        max_derailed=args.max_derailed,
        max_good=args.max_good,
        seed=args.seed,
        min_chars=args.min_chars,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    counts = {"good": 0, "derailed": 0}
    for p in pairs:
        counts[p["label"]] = counts.get(p["label"], 0) + 1
    logger.info("wrote %d pairs to %s (%s)", len(pairs), args.out, counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
