#!/usr/bin/env python3
"""Condense a verified drydock solve trajectory into a SHORT task→solution target.

The n=1 experiment showed distilling a raw 26-turn agentic trajectory memorizes but does
NOT transfer, while distilling a condensed (task → the winning file edits → run → verify)
target DOES. This builds that condensed form generally: keep the ORIGINAL instruction and
the LAST solution-producing edit per file (Write = full final content, supersedes earlier
edits of that file; Edit-only files keep their edit calls in order), THEN the
artifact-producing RUN step(s) — the Bash commands that execute the solution — plus a terse
verify/done. All research / investigation / dead-end turns are dropped.

P1 (2026-07-31): keep the RUN step. Many tbench tasks need a command to RUN (e.g.
`python3 solve.py` to write /app/dist.npy, or a build) — the checker inspects an artifact the
file edits alone don't produce. Earlier this dropped all Bash, so run-based tasks condensed to
an incomplete "write files but never run them" target that couldn't reproduce. Now we keep the
substantive Bash commands after the last file edit (the "execute it" phase); pure-Bash solves
(no file edits, e.g. a build) keep their substantive command sequence.

Usage:  general_condense.py <raw_traj.json> <out_condensed.json>
Emits a build_record-shaped trajectory (system, messages, verified, reward) that
`compass harvest-tbench` windows for training. Canary is left as-is (the train step
scrubs line-level); returns non-zero if there's no solution (no file edits AND no run step).
"""
import json
import sys

# commands that only inspect state (no side effect on the checked artifact) — dropped from the
# condensed RUN step unless they redirect output (`>`). Matched on the first bare token.
_INSPECT = {"ls", "pwd", "cd", "cat", "head", "tail", "grep", "find", "echo", "which",
            "stat", "file", "tree", "wc", "ps", "df", "env", "less", "more", "type", "man",
            "clear", "history", "whoami", "date", "printenv"}


def _is_inspection(cmd: str) -> bool:
    """True if the command only looks at state (safe to drop from the condensed run step)."""
    c = (cmd or "").strip()
    if not c or ">" in c:            # any redirect -> it produces something -> keep
        return False
    # first real token, skipping leading `sudo`/env-assignments
    for tok in c.split():
        if tok == "sudo" or "=" in tok.split("/")[0]:
            continue
        return tok.split("/")[-1] in _INSPECT
    return False


def _run_steps(msgs: list, after_idx: int, any_edit: bool) -> list[str]:
    """Substantive Bash commands to reproduce the solve. If there were file edits, keep the
    Bash AFTER the last edit (the execute phase); otherwise keep all substantive Bash. Dedups
    consecutive repeats and caps length to keep the target short."""
    cmds: list[str] = []
    idx = -1
    for m in msgs:
        if not (isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")):
            continue
        for tc in m["tool_calls"]:
            idx += 1
            if tc.get("name") != "Bash":
                continue
            if any_edit and idx <= after_idx:
                continue
            cmd = (tc.get("input") or {}).get("command") or (tc.get("input") or {}).get("cmd")
            if cmd and not _is_inspection(cmd):
                if not cmds or cmds[-1] != cmd:      # drop consecutive dupes
                    cmds.append(cmd)
    return cmds[-8:]


def condense(raw: dict) -> dict | None:
    msgs = raw.get("messages", [])
    instr = next((m.get("content") for m in msgs
                  if isinstance(m, dict) and m.get("role") == "user"), "")
    # walk assistant tool_calls in order; collect Write (full content) + Edit (patch) per file
    last_write: dict[str, dict] = {}     # file_path -> Write input (last wins)
    edits: dict[str, list] = {}          # file_path -> [Edit inputs in order]
    order: list[str] = []                # first-seen file order
    idx = -1                             # global tool-call index (across all assistant turns)
    last_edit_idx = -1                   # index of the last Write/Edit -> run steps come after it
    for m in msgs:
        if not (isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")):
            continue
        for tc in m["tool_calls"]:
            idx += 1
            name = tc.get("name")
            inp = tc.get("input") or {}
            fp = inp.get("file_path") or inp.get("path")
            if name not in ("Write", "Edit") or not fp:
                continue
            if fp not in order:
                order.append(fp)
            if name == "Write":
                last_write[fp] = inp
                edits.pop(fp, None)           # a full Write supersedes prior edits
            elif name == "Edit":
                if fp not in last_write:
                    edits.setdefault(fp, []).append(inp)
            last_edit_idx = idx
    run_cmds = _run_steps(msgs, last_edit_idx, any_edit=bool(order))
    # PRUNE dead files (2026-07-31): iterating solves leave many superseded file versions
    # (distribution-search wrote solve_kl.py..v8 + verify_kl.py but only RUNS v8+verify). If a run
    # command names specific files, keep ONLY those (drop the dead iterations) — a much cleaner,
    # more-reproducible target. If NO file is named by any run command (e.g. break-filter's out.html
    # is checked by an unrelated test import), keep ALL files — never drop a file we can't prove dead.
    referenced = {fp for fp in order
                  if any(fp.rsplit("/", 1)[-1] in c for c in run_cmds)}
    if referenced:
        order = [fp for fp in order if fp in referenced]
    # build the condensed solution tool_calls: file edits first, then the RUN step(s)
    sol_calls, tool_results = [], []
    for fp in order:
        if fp in last_write:
            sol_calls.append({"name": "Write", "input": last_write[fp]})
            tool_results.append({"role": "tool", "name": "Write", "content": f"Wrote {fp}"})
        elif fp in edits:
            for e in edits[fp]:
                sol_calls.append({"name": "Edit", "input": e})
                tool_results.append({"role": "tool", "name": "Edit", "content": f"Edited {fp}"})
    for cmd in run_cmds:
        sol_calls.append({"name": "Bash", "input": {"command": cmd}})
        tool_results.append({"role": "tool", "name": "Bash", "content": "(ran)"})
    if not sol_calls:
        return None                          # nothing to condense (no file edits AND no run step)
    _files = [fp for fp in order if fp in last_write or fp in edits]
    if _files and run_cmds:
        lead = f"Applying the fix to {', '.join(_files)}, then running it."
    elif _files:
        lead = f"Applying the fix to {', '.join(_files)}."
    else:
        lead = "Running the solution."
    condensed = {
        "task": raw.get("task", ""), "model": raw.get("model", ""),
        "objective": raw.get("objective", ""),
        "acceptance_criteria": raw.get("acceptance_criteria", []), "phase": "complete",
        "system": raw.get("system", ""),
        "messages": [
            {"role": "user", "content": instr},
            {"role": "assistant",
             "content": lead,
             "tool_calls": sol_calls},
            *tool_results,
            {"role": "assistant",
             "content": "Verified against the task's checker — the task now passes. Done."},
        ],
        "verified": True, "reward": 1,
    }
    return condensed


if __name__ == "__main__":
    raw = json.load(open(sys.argv[1]))
    out = condense(raw)
    if out is None:
        sys.stderr.write(f"nothing to condense (no file edits and no run step) in {sys.argv[1]}\n")
        sys.exit(2)
    json.dump(out, open(sys.argv[2], "w"))
    calls = out["messages"][1]["tool_calls"]
    n_files = len({(tc["input"].get("file_path") or tc["input"].get("path"))
                   for tc in calls if tc["name"] in ("Write", "Edit")})
    n_runs = sum(1 for tc in calls if tc["name"] == "Bash")
    print(f"condensed {out['task']}: {n_files} solution file(s), {n_runs} run step(s), "
          f"{len(out['messages'])} messages")
