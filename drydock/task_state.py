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


def _step_id(text: str) -> str:
    """Stable short id for a plan step, derived from its text (so the SAME step
    keeps its id across plan revisions). Deterministic — no randomness/time."""
    h = 0
    for ch in text.strip().lower():
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return f"s{h:08x}"


@dataclass
class RollingPlan:
    """A short, updateable plan (PRD Epic C): one ACTIVE step + a few pending ones,
    each with a stable id. Rebuilt from the checklist the model maintains; ids are
    text-derived so an unchanged step keeps its id across revisions."""
    steps: list[dict] = field(default_factory=list)  # {id, text, status}
    version: int = 0
    max_pending: int = 4

    def update_from_items(self, items: list[tuple[str, str]]) -> bool:
        """Sync from (text, status) checklist pairs. Caps pending steps (keeps the
        active + first max_pending pending). Returns True if the plan CHANGED."""
        steps = [{"id": _step_id(t), "text": t, "status": s} for t, s in items if t]
        # active = first in_progress, else first pending
        active = next((s for s in steps if s["status"] == "in_progress"), None)
        if active is None:
            active = next((s for s in steps if s["status"] == "pending"), None)
        kept, pending_kept = [], 0
        for s in steps:
            if s["status"] == "done" or s is active:
                kept.append(s)
            elif s["status"] == "pending" and pending_kept < self.max_pending:
                kept.append(s); pending_kept += 1
            elif s["status"] == "in_progress":
                kept.append(s)
        changed = [(s["id"], s["status"]) for s in kept] != [(s["id"], s["status"]) for s in self.steps]
        if changed:
            self.steps = kept
            self.version += 1
        return changed

    def active_step(self) -> dict | None:
        return next((s for s in self.steps if s["status"] == "in_progress"), None) \
            or next((s for s in self.steps if s["status"] == "pending"), None)

    def remaining(self) -> int:
        return sum(1 for s in self.steps if s["status"] != "done")

    def to_dict(self) -> dict:
        return {"steps": list(self.steps), "version": self.version}


@dataclass
class TaskState:
    """Authoritative task facts, independent of the message transcript."""
    objective: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    phase: str = "understand"
    plan: RollingPlan = field(default_factory=RollingPlan)

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
            "plan": self.plan.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskState:
        plan = RollingPlan()
        pd = d.get("plan") or {}
        plan.steps = list(pd.get("steps", []))
        plan.version = pd.get("version", 0)
        return cls(
            objective=d.get("objective", ""),
            acceptance_criteria=list(d.get("acceptance_criteria", [])),
            phase=d.get("phase", "understand"),
            plan=plan,
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
