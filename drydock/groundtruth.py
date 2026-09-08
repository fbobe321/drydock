"""Ground-truth ledger — separate what is KNOWN from what is ASSUMED, and rank the
unknowns by how much they could change the decision.

WHY THIS EXISTS, and why it is a LEDGER rather than a prompt
------------------------------------------------------------
A reasoning checklist was measured on terminal-bench-2 and did not work: applied to
every task it caused regressions (it overthinks work that was already succeeding), and
applied only after a failure it was BEATEN by simply retrying (4.6%/4.7% rescue vs a
plain retry's 6.2%). A longer ten-step variant did worse than a short one, and
compliance explained why — the long form was followed less often (criteria written 3/8
vs 7/8). More instructions bought less behaviour.

So this module deliberately does NOT tell the model how to think. It gives it somewhere
to PUT things, and it computes one ranking the model is bad at doing in its head.

The specific thing it computes — `next_test()` — comes from a failure in this project's
own research log rather than from theory. A claim about a prompt scaffold was reported
at +6.8 points, corrected to +3.4, then to +0.0. Every correction came from a control
that could have been run on day one; the highest-value unknown was always "would a plain
retry do the same thing?", and it went unasked for six days while cheaper, less
decisive experiments ran. Ranking unknowns by DECISION IMPACT (not by how interesting or
how easy they are) is the step that would have caught it immediately.

Advisory by contract: nothing here blocks a tool call, raises on bad input, or edits the
model's plan. It records, ranks, and renders. See also `predictions.py`, which closes the
other half of the loop (predict before you look).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

# Confidence bands. Kept coarse on purpose: a model asked for a 0-100 confidence emits
# noise, but "did I VERIFY this or am I assuming it" is a distinction it can actually make.
FACT = "fact"            # verified by looking (a file read, a command's output)
ASSUMPTION = "assumption"  # believed, not checked — the usual source of a stuck run
UNKNOWN = "unknown"        # explicitly not known
KINDS = (FACT, ASSUMPTION, UNKNOWN)


@dataclass
class Item:
    """One thing believed about the problem, and where that belief came from."""
    id: str
    kind: str = ASSUMPTION
    statement: str = ""
    # For FACT: how it was verified (the command, the file). Empty evidence on a FACT is
    # itself a smell — it means something got promoted without being checked.
    evidence: str = ""
    # For ASSUMPTION/UNKNOWN: how much the decision changes if this turns out false.
    # 0 = irrelevant, 3 = the approach is invalid. This is the field that does the work.
    impact: int = 0
    # Rough cost to find out (0 = seconds, 3 = hours). Used only to break impact ties, so
    # a cheap decisive test wins over an expensive one — never to avoid a decisive test.
    cost: int = 1
    resolved: bool = False

    def summary(self) -> str:
        mark = {FACT: "✓", ASSUMPTION: "?", UNKNOWN: "·"}.get(self.kind, "·")
        if self.resolved:
            mark = "✔"
        tag = f" [impact {self.impact}/cost {self.cost}]" if self.kind != FACT else ""
        return f"{mark} {self.id} {self.statement[:70]}{tag}"


class Ledger:
    """Facts, assumptions and unknowns for one task, persisted next to the run."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.items: list[Item] = []
        if self.path and self.path.exists():
            self.load()

    # ── recording ────────────────────────────────────────────────────────────
    def add(self, statement: str, kind: str = ASSUMPTION, *, evidence: str = "",
            impact: int = 0, cost: int = 1) -> Item:
        """Record a belief. Unknown kinds degrade to ASSUMPTION rather than raising —
        a ledger that throws would take down the turn it is supposed to be helping."""
        if kind not in KINDS:
            kind = ASSUMPTION
        it = Item(id=f"i{len(self.items) + 1}", kind=kind,
                  statement=(statement or "").strip(),
                  evidence=(evidence or "").strip(),
                  impact=max(0, min(3, int(impact or 0))),
                  cost=max(0, min(3, int(cost or 1))))
        self.items.append(it)
        self._save()
        return it

    def verify(self, item_id: str, evidence: str) -> Item | None:
        """Promote an assumption to a fact, with the evidence that earned it. This is the
        only path to FACT — a belief cannot become a fact by being restated."""
        for it in self.items:
            if it.id == item_id:
                it.kind = FACT
                it.evidence = (evidence or "").strip()
                it.resolved = True
                self._save()
                return it
        return None

    def refute(self, item_id: str, evidence: str) -> Item | None:
        """Mark an assumption FALSE. Kept rather than deleted: a refuted assumption is
        the most valuable row in the ledger — it is the one that changed the approach."""
        for it in self.items:
            if it.id == item_id:
                it.statement = f"[REFUTED] {it.statement}"
                it.kind = FACT
                it.evidence = (evidence or "").strip()
                it.resolved = True
                self._save()
                return it
        return None

    # ── the ranking that is the point of the module ──────────────────────────
    def open_uncertainties(self) -> list[Item]:
        return [i for i in self.items if not i.resolved and i.kind != FACT]

    def next_test(self) -> Item | None:
        """The unknown worth attacking first: highest decision-impact, cheapest to settle
        among equals. NOT the easiest, and NOT the most interesting — those are the two
        attractors that let a project run six days of experiments that could not have
        changed its own conclusion."""
        openq = [i for i in self.open_uncertainties() if i.impact > 0]
        if not openq:
            return None
        return sorted(openq, key=lambda i: (-i.impact, i.cost, i.id))[0]

    def unevidenced_facts(self) -> list[Item]:
        """FACTs carrying no evidence — i.e. assumptions wearing a fact's badge."""
        return [i for i in self.items if i.kind == FACT and not i.evidence]

    # ── rendering ────────────────────────────────────────────────────────────
    def render(self) -> str:
        if not self.items:
            return "(ledger empty)"
        lines = [i.summary() for i in self.items]
        nxt = self.next_test()
        if nxt:
            lines.append(f"→ test first: {nxt.id} ({nxt.statement[:60]}) "
                         f"— impact {nxt.impact}, cost {nxt.cost}")
        stale = self.unevidenced_facts()
        if stale:
            lines.append(f"⚠ {len(stale)} 'fact(s)' with no evidence: "
                         f"{', '.join(i.id for i in stale)}")
        return "\n".join(lines)

    # ── persistence ──────────────────────────────────────────────────────────
    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(i) for i in self.items], indent=2), encoding="utf-8")
        except OSError:
            pass          # advisory: never fail a run over bookkeeping

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path else []
            self.items = [Item(**{k: v for k, v in d.items()
                                  if k in Item.__dataclass_fields__}) for d in raw]
        except (OSError, json.JSONDecodeError, TypeError):
            self.items = []
