"""Classifier CLI — `python -m drydock.core.classifier <log>`.

Runs the rule set against a log file (or stdin) and prints one of:

    summary       — bucket counts + top pattern_ids (default)
    json          — JSONL of every FailureSignal
    text          — bucket / pattern_id / evidence per signal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from drydock.core.classifier.classifier import Classifier


def _read_input(path: str | None) -> str:
    if path and path != "-":
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="drydock.core.classifier",
        description="Classify failure observations into harness/retrieval/steering/model/input buckets.",
    )
    p.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Path to a log file, or '-' for stdin (default).",
    )
    p.add_argument(
        "--format",
        choices=["summary", "json", "text"],
        default="summary",
    )
    p.add_argument("--source", default="", help="Tag the source label on each signal.")
    p.add_argument("--session-id", default="")
    p.add_argument("--prompt-id", default="")
    p.add_argument(
        "--dispatch",
        action="store_true",
        help="After classifying, dispatch signals to ~/.drydock/dispatch/<bucket>.jsonl queues.",
    )
    p.add_argument(
        "--queue-root",
        default="",
        help="Override dispatch queue root (default: ~/.drydock/dispatch/).",
    )
    args = p.parse_args(argv)

    text = _read_input(args.input)
    classifier = Classifier(
        source=args.source or (args.input if args.input != "-" else "<stdin>"),
        session_id=args.session_id,
        prompt_id=args.prompt_id,
    )
    signals = classifier.classify_text(text)

    if args.dispatch:
        from pathlib import Path
        from drydock.core.classifier import Dispatcher
        queue_root = Path(args.queue_root) if args.queue_root else None
        dispatcher = Dispatcher(queue_root=queue_root)
        result = dispatcher.dispatch_all(signals)
        print(f"[dispatched] {result.summary()}", file=sys.stderr)

    if args.format == "json":
        for s in signals:
            print(json.dumps(s.to_jsonable()))
    elif args.format == "text":
        for s in signals:
            print(f"{s.bucket}\t{s.pattern_id}\t{s.evidence[:120]}")
    else:
        print(f"Total signals: {len(signals)}")
        if not signals:
            print("(no patterns matched)")
            return 0
        print()
        print("By bucket:")
        for bucket, count in sorted(
            Classifier.summarize(signals).items(),
            key=lambda kv: -kv[1],
        ):
            print(f"  {bucket:20s} {count}")
        print()
        print("Top patterns:")
        for pattern_id, count in Classifier.top_patterns(signals, n=10):
            print(f"  {count:4d}  {pattern_id}")
        print()
        print("Sample suggested actions:")
        seen_ids: set[str] = set()
        for s in signals:
            if s.pattern_id in seen_ids:
                continue
            seen_ids.add(s.pattern_id)
            print(f"  [{s.bucket}] {s.suggested_action}")
            if len(seen_ids) >= 5:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
