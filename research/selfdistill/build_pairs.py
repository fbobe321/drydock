#!/usr/bin/env python3
"""build_pairs.py — turn captured ratchet/eratchet rounds into CONTRASTIVE PREFERENCE pairs.

WHY (2026-08-21): the MoE->31B SFT run trained on a WINNER-ONLY corpus
(`*_solved_traj.json` = the transcript of the solving round only). Diagnosis showed
10/14 traces solved at round 1 and only 1/14 carried cross-round context, so the
ratchet's actual mechanism — preserve progress, retry differently, improve — was
never in the training signal. It could not learn what it was never shown.

A preference pair needs only "same starting point, one attempt scored better than
another" — NOT a solve. So this yields signal on tasks the model FAILS, which is
exactly where the headroom is, sidestepping the "fast generator => easy tasks =>
no headroom" trap the MoE verdict identified.

Mirrors drydock.eratchet.contrastive_pairs: group by (task, round-base), emit
(chosen, rejected) ordered by score margin, most decisive first.

Usage: build_pairs.py [capture_dir] [out.jsonl]
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

CAP = sys.argv[1] if len(sys.argv) > 1 else \
    "/data3/tbench_local/frontier/selfdistill/ratchet/capture"
OUT = sys.argv[2] if len(sys.argv) > 2 else \
    "/data3/tbench_local/frontier/selfdistill/moe_collect/contrastive_pairs.jsonl"


def _load(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    msgs = d.get("messages") or []
    if not any(m.get("role") == "assistant" for m in msgs):
        return None                      # no trainable signal
    return [{"role": "system", "content": d.get("system", "")}] + msgs


def main() -> int:
    man = os.path.join(CAP, "manifest.tsv")
    if not os.path.exists(man):
        print(f"no manifest yet at {man} — capture populates it as eratchet runs")
        return 1
    # group by the SHARED starting point: (task, base_ref). Variants/rounds from the
    # same base are directly comparable; across different bases they are not.
    groups = defaultdict(list)
    for line in open(man, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) < 7:
            continue
        task, rnd, var, mode, base, passed, total = p[:7]
        try:
            passed, total = int(passed), int(total)
        except ValueError:
            continue
        fn = f"r{rnd}_v{var}.json" if mode != "plain" else f"r{rnd}.json"
        path = os.path.join(CAP, task, fn)
        if os.path.exists(path):
            groups[(task, base)].append(
                {"task": task, "round": rnd, "mode": mode, "path": path,
                 "passed": passed, "total": total})

    pairs, seen = [], 0
    for (task, base), items in groups.items():
        for a in items:
            for b in items:
                if a is b or a["passed"] <= b["passed"]:
                    continue           # need a STRICT improvement to be a preference
                seen += 1
                ca, cb = _load(a["path"]), _load(b["path"])
                if not ca or not cb:
                    continue
                pairs.append({
                    "task": task, "base_ref": base,
                    "margin": a["passed"] - b["passed"],
                    "chosen":   {"messages": ca, "reward": a["passed"],
                                 "total": a["total"], "round": a["round"], "mode": a["mode"]},
                    "rejected": {"messages": cb, "reward": b["passed"],
                                 "total": b["total"], "round": b["round"], "mode": b["mode"]},
                })
    pairs.sort(key=lambda p: -p["margin"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    tasks = len({p["task"] for p in pairs})
    unsolved = sum(1 for p in pairs if p["chosen"]["reward"] < p["chosen"]["total"])
    print(f"groups={len(groups)} candidate-pairs={seen} written={len(pairs)} "
          f"tasks={tasks}")
    print(f"pairs whose WINNER still did not solve: {unsolved} "
          f"(these are the frontier signal a winner-only corpus can't give)")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
