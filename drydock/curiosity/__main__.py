"""CLI for the curiosity queue: list, mark consumed, stats.

Usage:
    python -m drydock.curiosity top [--limit N]           # show top unconsumed items
    python -m drydock.curiosity stats                     # counts by kind + consumed/pending
    python -m drydock.curiosity consume <id>              # mark a fingerprint as consumed
    python -m drydock.curiosity reset                     # clear consumed state (testing)

The "top" output is markdown — designed to be pasted into the
autonomous_review prompt so the cron-launched Claude run can pick the
highest-priority unconsumed signal and act on it (ingest, prompt rule,
AGENTS.md hint, etc.), then call `consume <id>` so it never gets
picked twice.

Priority order (highest first):
    HLE_FAILURE         — measurable benchmark signal; biggest ROI
    EVIDENCE_CONFLICT   — model is confidently wrong somewhere
    UNKNOWN_TERM        — user mentioned a thing we have no corpus for
    IDLE_EXPLORATION    — self-generated hypothesis (Phase 3)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from drydock.curiosity.item import CuriosityKind
from drydock.curiosity.queue import queue_path, read_recent


CONSUMED_STATE = Path.home() / ".drydock" / "dispatch" / ".curiosity_consumed.json"

# Priority — lower number = higher priority. Used to sort the "top" view.
_KIND_PRIORITY: dict[str, int] = {
    CuriosityKind.HLE_FAILURE.value: 0,
    CuriosityKind.EVIDENCE_CONFLICT.value: 1,
    CuriosityKind.UNKNOWN_TERM.value: 2,
    CuriosityKind.IDLE_EXPLORATION.value: 3,
}


def _load_consumed() -> set[str]:
    if not CONSUMED_STATE.is_file():
        return set()
    try:
        d = json.loads(CONSUMED_STATE.read_text())
        return set(d.get("ids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_consumed(ids: set[str]) -> None:
    CONSUMED_STATE.parent.mkdir(parents=True, exist_ok=True)
    CONSUMED_STATE.write_text(json.dumps({"ids": sorted(ids)}, indent=2))


def _all_items() -> list[dict]:
    """Read every queue entry, newest first."""
    items = read_recent(limit=10_000)
    return list(reversed(items))


def _rank(item: dict) -> tuple[int, float, str]:
    kind = item.get("kind", "")
    prio = _KIND_PRIORITY.get(kind, 99)
    conf = -float(item.get("confidence", 0.0))  # higher confidence first
    ts = item.get("ts", "")
    return (prio, conf, ts)


def cmd_top(args: argparse.Namespace) -> int:
    consumed = _load_consumed()
    items = [i for i in _all_items() if i.get("id") not in consumed]
    items.sort(key=_rank)
    items = items[: args.limit]

    if not items:
        print(f"### Curiosity queue\n\n_No unconsumed items._\n\nQueue: `{queue_path()}`")
        return 0

    lines = [f"### Curiosity queue — top {len(items)} unconsumed", ""]
    lines.append(f"Queue: `{queue_path()}` · "
                 f"Consumed state: `{CONSUMED_STATE}`")
    lines.append("")
    for i, item in enumerate(items, 1):
        kind = item.get("kind", "?")
        term = item.get("term", "")[:140]
        ctx = item.get("context", "")[:300].replace("\n", " ")
        src = item.get("source", "")
        conf = item.get("confidence", 0.0)
        action = item.get("suggested_action", "")
        fp = item.get("id", "?")
        lines.append(f"#### {i}. `{kind}` · conf={conf} · `{fp}`")
        lines.append(f"- **term:** {term}")
        if ctx:
            lines.append(f"- **context:** {ctx}")
        if src:
            lines.append(f"- **source:** {src}")
        if action:
            lines.append(f"- **suggested action:** {action}")
        lines.append("")
    lines.append(
        "After acting on an item, mark it consumed:  "
        "`python -m drydock.curiosity consume <id>`  "
        "Use the commit-message prefix  `addresses curiosity:<id>:` "
        "so future ticks dedupe."
    )
    print("\n".join(lines))
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    consumed = _load_consumed()
    items = _all_items()
    by_kind: dict[str, dict[str, int]] = {}
    for item in items:
        k = item.get("kind", "?")
        bucket = by_kind.setdefault(k, {"total": 0, "consumed": 0, "pending": 0})
        bucket["total"] += 1
        if item.get("id") in consumed:
            bucket["consumed"] += 1
        else:
            bucket["pending"] += 1
    print(f"Queue:     {queue_path()}")
    print(f"Consumed:  {CONSUMED_STATE}")
    print(f"Total:     {len(items)} items, {len(consumed)} consumed, "
          f"{len(items) - len(consumed)} pending")
    print()
    if not by_kind:
        print("(empty queue)")
        return 0
    print(f"{'kind':<22} {'total':>6} {'pending':>8} {'consumed':>9}")
    print("-" * 50)
    for k in sorted(by_kind, key=lambda x: _KIND_PRIORITY.get(x, 99)):
        b = by_kind[k]
        print(f"{k:<22} {b['total']:>6} {b['pending']:>8} {b['consumed']:>9}")
    return 0


def cmd_consume(args: argparse.Namespace) -> int:
    consumed = _load_consumed()
    consumed.add(args.id)
    _save_consumed(consumed)
    print(f"Marked {args.id} consumed. Total: {len(consumed)}")
    return 0


def cmd_reset(_args: argparse.Namespace) -> int:
    if CONSUMED_STATE.is_file():
        CONSUMED_STATE.unlink()
        print(f"Cleared {CONSUMED_STATE}")
    else:
        print("No consumed-state file to clear.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drydock.curiosity",
                                 description="Curiosity queue CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_top = sub.add_parser("top", help="show top unconsumed items as markdown")
    ap_top.add_argument("--limit", type=int, default=5)
    ap_top.set_defaults(func=cmd_top)

    ap_stats = sub.add_parser("stats", help="counts by kind + consumed/pending")
    ap_stats.set_defaults(func=cmd_stats)

    ap_consume = sub.add_parser("consume",
                                help="mark a fingerprint as consumed")
    ap_consume.add_argument("id")
    ap_consume.set_defaults(func=cmd_consume)

    ap_reset = sub.add_parser("reset", help="clear consumed state")
    ap_reset.set_defaults(func=cmd_reset)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
