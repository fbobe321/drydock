"""Retriever interface for GraphRAG — pluggable backend contract.

The harness talks to retrievers through this module only. A v0 deployment
can use the bundled `Index`-backed retriever; a customer with their own
embedding stack can implement `Retriever` against their own backend, drop
it into the harness, and the agent loop never knows the difference.

Result shapes are intentionally narrow:
- `SymbolHit` — for code-graph lookups: definition site of a class/function
  with optional parent-class chain.
- `TextHit` — for chunked text retrieval: the chunk content, its source
  file/line, and a score the caller can use to threshold.

`RetrievalResult` bundles both so a single query can return mixed evidence.
The `citation_id` on each hit is the load-bearing field — RAG flows in the
harness must require the model to reference it, so grounding can be
verified by an evaluator (per SOVEREIGN_PRD §10).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SymbolHit:
    """A class/function/method definition site in the indexed corpus."""
    name: str               # bare symbol name, e.g. "Request"
    qualname: str           # qualified name, e.g. "flask.wrappers.Request"
    kind: str               # "class" | "function" | "method"
    file: str               # absolute path
    line: int               # 1-based
    parents: tuple[str, ...] = ()   # base-class names if kind == "class"
    docstring: str | None = None
    citation_id: str = ""   # stable id for grounding checks (file:line)

    def format(self) -> str:
        head = f"{self.kind} {self.qualname} at {self.file}:{self.line}"
        if self.parents:
            head += f" (extends {', '.join(self.parents)})"
        if self.docstring:
            head += f"\n  {self.docstring.strip().splitlines()[0][:120]}"
        return head


@dataclass(frozen=True)
class TextHit:
    """A retrieved chunk of unstructured text (markdown, prose docs)."""
    content: str
    file: str
    start_line: int          # 1-based, inclusive
    end_line: int            # 1-based, inclusive
    score: float             # backend-defined; higher == more relevant
    citation_id: str = ""    # stable id for grounding checks

    def format(self) -> str:
        head = f"{self.file}:{self.start_line}-{self.end_line} (score={self.score:.3f})"
        body = self.content.strip()
        if len(body) > 400:
            body = body[:400] + "..."
        return f"{head}\n{body}"


@dataclass(frozen=True)
class WorkedExampleHit:
    """A previously-solved problem with its full reasoning chain.

    The "second brain" payload — when the model retrieves this, it sees
    not just facts but how a similar problem was worked through:
    intermediate steps, mistakes corrected, the final answer. Closer to
    how a human reasons by analogy than flat-chunk retrieval.
    """
    problem_text: str         # the original problem statement
    category: str             # e.g. "Physics", "Math" — coarse domain
    subject: str              # finer subdomain, e.g. "holographic-models"
    reasoning_steps: tuple[str, ...]   # ordered: first step → last step
    final_answer: str
    source: str               # where it came from: "hle:<id>", "manual", "session:<sid>"
    score: float              # backend-defined; higher == more relevant
    citation_id: str = ""

    def format(self) -> str:
        head = (
            f"[worked example] {self.category}"
            + (f" / {self.subject}" if self.subject else "")
            + f" (score={self.score:.3f}) source={self.source}"
        )
        body_lines = [head, "Problem:", "  " + self.problem_text.strip()[:500]]
        if self.reasoning_steps:
            body_lines.append("Reasoning:")
            for i, step in enumerate(self.reasoning_steps, 1):
                body_lines.append(f"  {i}. {step.strip()[:300]}")
        body_lines.append(f"Answer: {self.final_answer.strip()[:200]}")
        return "\n".join(body_lines)


@dataclass
class RetrievalResult:
    """Output of a retrieve() call. Any subset of lists may be empty."""
    symbols: list[SymbolHit] = field(default_factory=list)
    text: list[TextHit] = field(default_factory=list)
    worked_examples: list[WorkedExampleHit] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.symbols and not self.text and not self.worked_examples

    def format(self) -> str:
        sections: list[str] = []
        # Worked examples first — they're the highest-signal payload when
        # they match. The model's prompt-attention is biased to the start.
        if self.worked_examples:
            sections.append("=== WORKED EXAMPLES ===")
            sections.extend(h.format() for h in self.worked_examples)
        if self.symbols:
            sections.append("=== SYMBOLS ===")
            sections.extend(h.format() for h in self.symbols)
        if self.text:
            sections.append("=== TEXT ===")
            sections.extend(h.format() for h in self.text)
        return "\n\n".join(sections) if sections else "(no results)"


@runtime_checkable
class Retriever(Protocol):
    """Pluggable retriever contract. Implementations swap freely."""

    def retrieve(
        self,
        query: str,
        *,
        symbol_limit: int = 5,
        text_limit: int = 5,
    ) -> RetrievalResult:
        """Run a query against the index. Should never raise on empty/no-match;
        return an empty `RetrievalResult` instead."""
        ...

    def find_symbol(self, name: str) -> list[SymbolHit]:
        """Lookup a symbol by exact or qualified name. Used for the
        cross-package inheritance case (pattern 4 in MODEL_SHORTCOMINGS)."""
        ...

    def inheritance_chain(self, qualname: str) -> list[SymbolHit]:
        """Walk the parent chain of a class. Returns hits from the queried
        class down to the deepest known ancestor in the index."""
        ...
