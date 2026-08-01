#!/usr/bin/env python3
"""corpus_rag.py — RSI corpus retrieval for the collection assist (P4).

Given the task the collector is currently attempting, retrieve the most SIMILAR *already-solved*
condensed trace from the growing corpus and emit a short "a related task was solved like this"
reference. As the corpus grows, solving new base-failures gets easier — the compounding loop.

Hard safety: NEVER retrieve the current task's own solution (that would be cheating), and NEVER
retrieve any HELD-OUT task (that would leak the eval set). corpus/raw contains only trained traces,
and we additionally exclude the query task + every name in heldout.txt.

Usage: corpus_rag.py <task_name> <instruction_file> <corpus_raw_dir> [heldout_file] [top_k]
Prints a formatted reference block to stdout (empty if nothing similar enough). Stdlib only.
"""
import json
import math
import os
import re
import sys

_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    d: dict[str, float] = {}
    for t in tokens:
        d[t] = d.get(t, 0.0) + 1.0
    return d


def _cosine(a: dict, b: dict, idf: dict) -> float:
    keys = set(a) | set(b)
    va = {k: a.get(k, 0.0) * idf.get(k, 0.0) for k in keys}
    vb = {k: b.get(k, 0.0) * idf.get(k, 0.0) for k in keys}
    dot = sum(va[k] * vb[k] for k in keys)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return dot / (na * nb) if na and nb else 0.0


def _instruction(rec: dict) -> str:
    for m in rec.get("messages", []):
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content") or ""
    return rec.get("objective", "")


def _solution_summary(path: str) -> str:
    """Condense the trace and describe its solution (files + run steps) tersely."""
    try:
        sys.path.insert(0, "/data3/compass")
        from general_condense import condense  # reuse the canonical condenser
        c = condense(json.load(open(path)))
        if not c:
            return ""
        calls = c["messages"][1]["tool_calls"]
        parts = []
        for tc in calls:
            if tc["name"] in ("Write", "Edit"):
                fp = tc["input"].get("file_path") or tc["input"].get("path")
                parts.append(f"edit {fp}")
            elif tc["name"] == "Bash":
                parts.append(f"run `{tc['input'].get('command', '')[:120]}`")
        return "; ".join(parts)
    except Exception:
        return ""


def retrieve(task: str, instruction: str, corpus_dir: str,
            heldout: set[str], top_k: int = 1, min_sim: float = 0.05) -> str:
    files = [f for f in os.listdir(corpus_dir) if f.endswith(".json")]
    docs = []  # (name, tokens, path)
    for f in files:
        name = f[:-5]
        if name == task or name in heldout:      # never the query task or a held-out task
            continue
        try:
            rec = json.load(open(os.path.join(corpus_dir, f)))
        except Exception:
            continue
        docs.append((name, _tokens(_instruction(rec)), os.path.join(corpus_dir, f)))
    if not docs:
        return ""
    # idf over the corpus
    df: dict[str, float] = {}
    for _, toks, _ in docs:
        for t in set(toks):
            df[t] = df.get(t, 0.0) + 1.0
    n = len(docs)
    idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    q = _tf(_tokens(instruction))
    scored = sorted(((_cosine(q, _tf(toks), idf), name, path)
                     for name, toks, path in docs), reverse=True)
    picks = [(s, name, path) for s, name, path in scored[:top_k] if s >= min_sim]
    if not picks:
        return ""
    blocks = []
    for sim, name, path in picks:
        summ = _solution_summary(path)
        if summ:
            blocks.append(f"- A previously-solved task ({name}) used this approach: {summ}")
    if not blocks:
        return ""
    return ("\n\nRELATED SOLVED WORK (from your own past verified solves — adapt the APPROACH, do not "
            "copy blindly; the specifics differ and you must verify with THIS task's checker):\n"
            + "\n".join(blocks))


if __name__ == "__main__":
    task = sys.argv[1]
    instruction = open(sys.argv[2]).read() if os.path.isfile(sys.argv[2]) else sys.argv[2]
    corpus_dir = sys.argv[3]
    heldout_file = sys.argv[4] if len(sys.argv) > 4 else ""
    top_k = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    heldout = set()
    if heldout_file and os.path.isfile(heldout_file):
        heldout = {l.strip() for l in open(heldout_file) if l.strip()}
    sys.stdout.write(retrieve(task, instruction, corpus_dir, heldout, top_k))
