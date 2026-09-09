#!/usr/bin/env python3
"""Ledger-USAGE signal for the loop-control experiment (p2 in PRE_REGISTRATION.md).

Reads the HOST-SIDE trajectory captures the ratchet writes per round
(ratchet/capture/<task>/r*.json), which survive container teardown.

IMPORTANT — what this can and cannot measure. The trajectory's own `tools` field
(trajectory.py) is built from messages with role=="tool": it lists the tools the
model actually CALLED, not the tools it was OFFERED. So a trajectory can prove
USAGE but says nothing about EXPOSURE. Do NOT infer "the tool was unavailable"
from an absence here — that is exactly the misread that made pilot #1 look like a
real null. Exposure is guarded separately and up front by the preflight in
run_loop_control.sh (a throwaway container that runs the real select_tools →
filter_tool_schemas pipeline and aborts the experiment if Ledger is not offered).

Given exposure is already established, this reports the p2 signal proper:

  calls=<total Ledger tool invocations across rounds>;used_rounds=<rounds with >=1 call>/<rounds>

Prints one field string on stdout, e.g. "calls=3;used_rounds=2/6". Never raises.
"""
from __future__ import annotations

import glob
import json
import os
import sys


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
    total_calls = 0
    used_rounds = 0
    for f in files:
        try:
            with open(f) as fh:
                d = json.load(fh)
        except Exception:
            continue
        rounds += 1
        k = _ledger_calls(d.get("messages"))
        total_calls += k
        if k > 0:
            used_rounds += 1
    print(f"calls={total_calls};used_rounds={used_rounds}/{rounds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
