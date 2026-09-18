"""Research telemetry for the compute-optimal agent-scaling study
(docs/compute_optimal_agent_scaling_prd.md §20/§21 — Phase 1 instrumentation).

Stdlib-only and swallow-all-I/O (the drydock/events.py idiom): instrumentation must
NEVER break or slow a real search run, and it does not alter search behaviour — it
only records what Ratchet/ERatchet/Swarm already produce (per-attempt fitness,
lineage, passing-check identity, compute) into a stable JSONL the scaling-law
analysis reads OFFLINE. It is a record of real runs, not an eval/judge harness.

Records live under <root>/.drydock/research/<experiment_id>/attempts.jsonl. Each
row is one agent attempt; `parent`/`parent_b` link the evolutionary lineage (§21).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1


def fitness(passed: int, total: int) -> float:
    """Graded fitness F = P/T (§9); 0.0 when nothing is gradeable yet."""
    return (passed / total) if total else 0.0


def deltas(parent_desc, child_desc) -> "tuple[list, list]":
    """(new_tests, lost_tests) between a parent's and child's passing-check sets
    (§9/§13). Descriptors are the *identities* of passing checks, so this captures
    which checks a descendant newly solved and which it regressed."""
    p = set(parent_desc or [])
    c = set(child_desc or [])
    return sorted(c - p), sorted(p - c)


@dataclass
class AttemptRecord:
    """One agent attempt (§20). Fields default so partial records are still valid;
    tokens/runtime are 0 when a producer can't supply them yet."""
    experiment_id: str
    task_id: str = ""
    model: str = ""
    strategy: str = ""                    # ratchet | eratchet | swarm | best_of_n | retry
    generation: int = 0
    agent: int = 0                        # index within the generation/wave
    attempt_id: str = ""                  # stable id, e.g. "g7v4"
    parent: Optional[str] = None          # lineage: parent attempt_id or base ref
    parent_b: Optional[str] = None        # crossover second parent (§13)
    tokens_in: int = 0
    tokens_out: int = 0
    runtime_seconds: float = 0.0
    fitness_before: Optional[float] = None
    fitness_after: Optional[float] = None
    tests_before: str = ""                # "P/T"
    tests_after: str = ""                 # "P/T"
    passed: int = 0
    total: int = 0
    new_tests: list = field(default_factory=list)   # checks this attempt newly passes
    lost_tests: list = field(default_factory=list)   # checks it regressed
    descriptor: Optional[list] = None      # sorted identities of passing checks (§11)
    checkpoint: bool = False               # improvement locked in (PAWL)
    rollback: bool = False                 # regressed → restored to best
    archive_added: bool = False            # entered the quality-diversity archive (§12)
    server: str = ""
    spec: Optional[dict] = None            # variation operator / temperature
    ts: float = field(default_factory=time.time)
    schema: int = SCHEMA_VERSION


class TelemetryLog:
    """Append-only JSONL sink for AttemptRecords, one dir per experiment."""

    def __init__(self, experiment_id: str, root: str = "."):
        self.experiment_id = experiment_id
        self.dir = Path(root) / ".drydock" / "research" / experiment_id
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.path = self.dir / "attempts.jsonl"

    def record(self, rec) -> dict:
        """Append one record (AttemptRecord or plain dict). Returns the dict written
        (or that would have been written) — never raises."""
        d = asdict(rec) if isinstance(rec, AttemptRecord) else dict(rec)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(d, default=str, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            pass
        return d

    def read(self) -> list:
        """All records in order; tolerant of a partial/corrupt trailing line."""
        out: list = []
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return out

    def children(self) -> dict:
        """parent attempt_id -> [child attempt_ids], reconstructing the lineage
        tree/DAG (§21) from the persisted parent links."""
        kids: dict = {}
        for r in self.read():
            p = r.get("parent")
            if p:
                kids.setdefault(p, []).append(r.get("attempt_id"))
        return kids
