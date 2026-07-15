"""Sub-agent specifications + structured reports (PRD Epic R).

Drydock already spawns sub-agents in fresh, isolated contexts (the read-only
`task`/`Dispatch` tools and the writable `Worker`). This formalises the shape of
one behind a WorkerSpec — the scoped objective, the tools it may use, whether
it's read-only, and its budgets — so the configuration is explicit, serialisable
(for later Admiral orchestration), and the read-only guarantee is STRUCTURAL:
a read-only spec cannot expose a mutating tool no matter what it was handed.

WorkerResult is the structured report a sub-agent hands back — a summary plus the
files it touched — instead of an opaque blob. All logic original to Drydock.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Tools that change state — never available to a read-only worker.
MUTATING_TOOLS = frozenset({
    "Write", "Edit", "GitCommit", "GraphAdd", "BuildKnowledge", "StigSet",
})


@dataclass
class WorkerSpec:
    """An isolated sub-agent's contract (PRD Req R1)."""
    objective: str
    allowed_tools: list[str] = field(default_factory=list)
    read_only: bool = True
    system_prompt: str = ""
    max_turns: int = 24
    max_tool_calls: int = 20
    summary_cap: int = 4000
    worker_id: str = ""

    def enforced_tools(self) -> list[str]:
        """The tools this worker may ACTUALLY use — a read-only worker has every
        mutating tool stripped, so it cannot edit files even if the caller listed
        one (PRD R1.2). Order-preserving; deduped."""
        seen: set[str] = set()
        out: list[str] = []
        for t in self.allowed_tools:
            if self.read_only and t in MUTATING_TOOLS:
                continue
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "objective": self.objective,
            "allowed_tools": self.enforced_tools(),
            "read_only": self.read_only,
            "max_turns": self.max_turns,
            "max_tool_calls": self.max_tool_calls,
        }


@dataclass
class WorkerResult:
    """A sub-agent's structured report back to the parent (PRD R1.3)."""
    worker_id: str
    summary: str
    changed_files: list[str] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "ok": self.ok,
        }
