#!/usr/bin/env python3
"""Retroactively dedupe `~/.drydock/dispatch/*.jsonl` queues.

The 2026-05-14 fingerprint-dedup fix
(`drydock.core.classifier.dispatcher`) prevents future amplification,
but the queues on disk are already inflated (e.g. harness.jsonl had
73.9× amplification on `thinking_stall`, 23,440 entries across only
317 unique evidence strings).

This script reads each queue, drops entries whose
(pattern_id, evidence) fingerprint has already been seen IN-FILE,
writes the deduped result back, and seeds the matching `.fp_<bucket>`
sidecar file with the surviving fingerprints so future writes pick
up where this cleanup left off.

Dry run by default; pass `--apply` to actually rewrite the files.

Outputs a per-queue before/after table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

QUEUE_ROOT = Path.home() / ".drydock" / "dispatch"


def _hash(parts: list[str]) -> str:
    return hashlib.sha1("\0".join(p or "" for p in parts).encode("utf-8", errors="replace")).hexdigest()


def _signal_fingerprint(rec: dict) -> str:
    """Classifier signals: dedupe by (pattern_id, evidence). Matches
    the dispatcher's `_signal_fingerprint`."""
    return _hash([rec.get("pattern_id"), rec.get("evidence")])


def _curiosity_fingerprint(rec: dict) -> str:
    """Curiosity items: each has a stable `id` computed from a 7-day
    rolling fingerprint of (kind, term, source). Use that directly so
    we don't squash items that share evidence but represent distinct
    queue entries (different question, different timestamp, etc.)."""
    cid = rec.get("id")
    if cid:
        return f"id:{cid}"
    # Fallback: synthesize from the natural identity tuple.
    return _hash([
        rec.get("kind"), rec.get("term"), rec.get("source"),
    ])


def _generic_fingerprint(rec: dict) -> str:
    """Last-resort: serialize a stable subset of fields."""
    return _hash([
        rec.get("pattern_id"), rec.get("evidence"),
        rec.get("kind"), rec.get("term"), rec.get("source"),
    ])


def _fingerprint_for(queue_name: str, rec: dict) -> str:
    if queue_name == "curiosity":
        return _curiosity_fingerprint(rec)
    # All classifier-signal queues (harness / retrieval / model_prior
    # / steering / ambiguous_input / other) share the same shape:
    # dedupe by (pattern_id, evidence).
    if rec.get("pattern_id"):
        return _signal_fingerprint(rec)
    return _generic_fingerprint(rec)


def _dedupe_queue(jsonl: Path, *, apply: bool) -> tuple[int, int, list[str]]:
    """Return (before, after, surviving_fingerprints). If apply=True,
    also rewrite the jsonl in place and seed .fp_<bucket>."""
    before = 0
    surviving_lines: list[str] = []
    surviving_fps: list[str] = []
    seen: set[str] = set()
    bucket = jsonl.stem

    for line in jsonl.read_text(errors="replace").splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        before += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            # Keep malformed lines intact — operator may want to inspect.
            surviving_lines.append(line)
            continue
        fp = _fingerprint_for(bucket, r)
        if fp in seen:
            continue
        seen.add(fp)
        surviving_lines.append(line)
        surviving_fps.append(fp)

    after = len(surviving_lines)

    if apply:
        # Write deduped queue back.
        jsonl.write_text("\n".join(surviving_lines) + ("\n" if surviving_lines else ""))
        # Seed the fingerprint sidecar. Only the classifier-signal
        # queues use the `.fp_<bucket>` filename — curiosity/retrieval
        # have their own dedup mechanisms in their producers.
        if bucket not in ("curiosity", "retrieval"):
            fp_path = jsonl.parent / f".fp_{bucket}"
            fp_path.write_text("\n".join(surviving_fps) + ("\n" if surviving_fps else ""))

    return before, after, surviving_fps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually rewrite files (default: dry run).")
    ap.add_argument("--queue-root", type=Path, default=QUEUE_ROOT,
                    help="Override the default queue root.")
    args = ap.parse_args()

    root = args.queue_root
    if not root.is_dir():
        print(f"queue root not found: {root}", file=sys.stderr)
        return 2

    jsonls = sorted(root.glob("*.jsonl"))
    if not jsonls:
        print(f"no .jsonl queues under {root}", file=sys.stderr)
        return 0

    mode = "apply" if args.apply else "dry-run"
    print(f"dedup mode: {mode}")
    print(f"{'queue':<30s}  {'before':>8s}  {'after':>8s}  {'dropped':>8s}  {'amp':>6s}")
    total_before = 0
    total_after = 0
    for jsonl in jsonls:
        before, after, _ = _dedupe_queue(jsonl, apply=args.apply)
        dropped = before - after
        amp = (before / after) if after else 0
        print(f"{jsonl.name:<30s}  {before:>8d}  {after:>8d}  {dropped:>8d}  {amp:>5.1f}×")
        total_before += before
        total_after += after
    print(f"{'TOTAL':<30s}  {total_before:>8d}  {total_after:>8d}  "
          f"{total_before - total_after:>8d}")
    if not args.apply:
        print("\n(dry run — pass --apply to rewrite files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
