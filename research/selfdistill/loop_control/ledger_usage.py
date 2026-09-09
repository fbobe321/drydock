#!/usr/bin/env python3
"""Ledger-usage signal for the loop-control experiment (p2 in PRE_REGISTRATION.md).

Reads the HOST-SIDE trajectory captures the ratchet already writes per round
(ratchet/capture/<task>/r*.json) — these survive container teardown, unlike the
old `docker exec` probe that always read `torndown`. Reports two things the pilot
could not measure:

  exposed=<rounds the Ledger schema was actually OFFERED>/<rounds>
  calls=<total Ledger tool invocations across rounds>

`exposed` is the wiring guard: if it is 0/N the tool never reached the model
(wrong wheel / pin not applied) and a null solve-delta means nothing. `calls` is
the p2 usage signal proper: the model choosing to use the artifact.

Prints one field string on stdout, e.g. "exposed=6/6;calls=3". Never raises.
"""
from __future__ import annotations

import glob
import json
import os
import sys


def _tool_names(tools: object) -> list[str]:
    if not isinstance(tools, list):
        return []
    out: list[str] = []
    for t in tools:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            n = t.get("name") or t.get("function", {}).get("name")
            if n:
                out.append(n)
    return out


def _ledger_calls(messages: object) -> int:
    if not isinstance(messages, list):
        return 0
    n = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        for tc in m.get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("name") == "Ledger":
                n += 1
        # defensive: some providers nest calls in content blocks
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") in ("tool_use", "tool_call") \
                        and b.get("name") == "Ledger":
                    n += 1
    return n


def main() -> int:
    capdir = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(glob.glob(os.path.join(capdir, "r*.json")))
    if not files:
        print("no-capture")
        return 0
    rounds = 0
    exposed = 0
    calls = 0
    for f in files:
        try:
            with open(f) as fh:
                d = json.load(fh)
        except Exception:
            continue
        rounds += 1
        if "Ledger" in _tool_names(d.get("tools")):
            exposed += 1
        calls += _ledger_calls(d.get("messages"))
    print(f"exposed={exposed}/{rounds};calls={calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
