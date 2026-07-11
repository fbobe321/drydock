"""Structured task state — the authoritative objective + acceptance criteria kept
OUTSIDE the chat transcript, so they survive context compaction and the model never
loses sight of the goal on a long task.

First increment toward the Agent-Buildout PRD (Epic A: Structured Task State /
Principle 4.2 "structured state is authoritative"). Deliberately small — a dataclass
the agent loop populates once and re-injects as a compact anchor after compaction —
so it drops into the current loop without a rewrite. All logic original to Drydock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Requirement-like lines: numbered (1. / 1)) or bulleted (- * •).
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$", re.M)
# Imperative "must/should/exactly/required" sentences (fallback when there's no list).
_IMPERATIVE = re.compile(r"\b(must|should|exactly|required|ensure|has to|needs? to)\b", re.I)


def extract_acceptance_criteria(objective: str, limit: int = 10) -> list[str]:
    """Pull discrete acceptance criteria out of the objective: numbered/bulleted
    lines first, else imperative must/should sentences. Best-effort; never raises."""
    if not objective or not isinstance(objective, str):
        return []
    items = [m.group(1).strip() for m in _LIST_ITEM.finditer(objective)]
    if not items:
        for s in re.split(r"(?<=[.!?])\s+", objective):
            s = s.strip()
            if s and len(s) < 240 and _IMPERATIVE.search(s):
                items.append(s)
    # de-dupe preserving order
    seen: set[str] = set()
    out = []
    for it in items:
        k = it.lower()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out[:limit]


@dataclass
class TaskState:
    """Authoritative task facts, independent of the message transcript."""
    objective: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    phase: str = "understand"

    @classmethod
    def from_objective(cls, objective: str) -> TaskState:
        obj = (objective or "").strip() if isinstance(objective, str) else str(objective or "").strip()
        return cls(objective=obj, acceptance_criteria=extract_acceptance_criteria(obj))

    def is_set(self) -> bool:
        return bool(self.objective)

    def to_dict(self) -> dict:
        """Serializable WITHOUT the transcript — the task can be reconstructed from this."""
        return {
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
            "phase": self.phase,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskState:
        return cls(
            objective=d.get("objective", ""),
            acceptance_criteria=list(d.get("acceptance_criteria", [])),
            phase=d.get("phase", "understand"),
        )

    def anchor_text(self) -> str:
        """A compact, durable reminder re-injected after compaction so a summarized
        transcript can never lose the original objective + criteria. '' if unset."""
        if not self.objective:
            return ""
        head = self.objective if len(self.objective) <= 700 else self.objective[:700] + " …"
        lines = [f"[TASK — do not lose sight of the original objective]\n{head}"]
        if self.acceptance_criteria:
            lines.append("Acceptance criteria (all must be met):")
            lines += [f"  - {c}" for c in self.acceptance_criteria]
        return "\n".join(lines)
