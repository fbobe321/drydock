"""Prolog tool — logic-programming via pytholog (pure-Python).

Companion to `solve` (Z3 / SMT): where Z3 reasons over arithmetic +
boolean constraints, Prolog reasons over facts and rules with
unification + backtracking. Strengths:

  - Family/relation puzzles (parent/grandparent/uncle/...)
  - Logic puzzles with rules like "if X then Y, if Y then Z"
  - Recursive predicates (Fibonacci, ancestor, path-finding)
  - "All X such that R(X, Y) holds" queries

Why pytholog instead of swipl: the user's deployment constraint is
"pip-installable, no system install". pytholog (pip install pytholog)
covers the Horn-clause + unification subset of Prolog. CLP / DCG /
arithmetic-heavy work should still go to `solve`.

Operations (`op=`):

  query(facts, rules, query)
      Run a single query against the given KB. Returns the list of
      variable bindings that satisfy the query, or `[]` for no match,
      or `["Yes"]` for a ground query that holds with no variables.

  consult(facts, rules)
      Validate that the KB compiles. Useful as a dry-run before
      issuing real queries. Returns "ok" or an error message.

  assert_and_query(facts, rules, query)
      Same as query but also returns the KB size (fact + rule count)
      so the model can see how big the program got.

Input syntax — standard Prolog (pytholog dialect):
    facts:  ["parent(tom, bob)", "parent(bob, ann)", ...]
    rules:  ["grandparent(X, Y) :- parent(X, Z), parent(Z, Y)", ...]
    query:  "grandparent(tom, X)"

Lowercase identifiers are atoms; uppercase identifiers are variables.

Read-only, side-effect free, ALWAYS permission. Hard-bounded:
  - Max 200 facts + 50 rules per call (KB build is O(n) so larger
    KBs work but tax context; this is a sanity ceiling).
  - 5-second query timeout (asyncio.wait_for).
  - Result list capped at 100 bindings (Prolog can backtrack
    indefinitely on cyclic rules).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, ClassVar, Literal, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from drydock.core.tools.ui import (
    ToolCallDisplay,
    ToolResultDisplay,
    ToolUIData,
)
from drydock.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from drydock.core.types import ToolResultEvent

logger = logging.getLogger("drydock.prolog")


PrologOp = Literal["query", "consult", "assert_and_query"]


_OP_HELP = (
    "Operation: query (run a query against facts+rules), consult "
    "(validate the KB compiles without querying), assert_and_query "
    "(same as query but also returns KB size)."
)


class PrologArgs(BaseModel):
    op: PrologOp = Field(description=_OP_HELP)
    facts: list[str] = Field(
        default_factory=list,
        description=(
            'Ground facts, one per entry. Standard Prolog syntax: '
            '`parent(tom, bob)`, `likes(alice, pizza)`. Lowercase '
            'atoms, no trailing period. Max 200.'
        ),
    )
    rules: list[str] = Field(
        default_factory=list,
        description=(
            'Inference rules, one per entry. Standard Prolog syntax: '
            '`grandparent(X, Y) :- parent(X, Z), parent(Z, Y)`. '
            'Uppercase identifiers are variables. Max 50.'
        ),
    )
    query: str = Field(
        default="",
        description=(
            'Single query to run (e.g. `grandparent(tom, X)`). Variables '
            'in the query become the unknowns to solve for. Ignored by '
            '`consult`.'
        ),
    )
    timeout_sec: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="Query timeout in seconds. Default 5.",
    )


class PrologResult(BaseModel):
    ok: bool
    op: str = ""
    bindings: list[dict] = Field(default_factory=list)
    # Convenience: "Yes" / "No" string for ground (no-variables) queries
    # or 0 bindings.
    summary: str = ""
    kb_size: int = 0  # facts + rules count
    error: str = ""


class PrologConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Validation ─────────────────────────────────────────────────────────

_MAX_FACTS = 200
_MAX_RULES = 50
_MAX_LEN_PER_ENTRY = 1000


_FORBIDDEN_TOKENS = (
    "import", "exec", "eval", "open(", "__",
    "globals", "locals", "getattr",
)


def _validate_kb(facts: list[str], rules: list[str]) -> None:
    if len(facts) > _MAX_FACTS:
        raise ToolError(f"too many facts ({len(facts)} > {_MAX_FACTS})")
    if len(rules) > _MAX_RULES:
        raise ToolError(f"too many rules ({len(rules)} > {_MAX_RULES})")
    for kind, items in (("fact", facts), ("rule", rules)):
        for entry in items:
            if not entry or not entry.strip():
                continue  # skip empty
            if len(entry) > _MAX_LEN_PER_ENTRY:
                raise ToolError(
                    f"{kind} too long ({len(entry)} chars > {_MAX_LEN_PER_ENTRY})"
                )
            low = entry.lower()
            for bad in _FORBIDDEN_TOKENS:
                if bad in low:
                    raise ToolError(f"forbidden token {bad!r} in {kind}: {entry!r}")


def _build_kb(facts: list[str], rules: list[str]):
    """Compile a pytholog KnowledgeBase from the inputs."""
    import pytholog as pl
    kb = pl.KnowledgeBase("drydock_session")
    # pytholog accepts a single list of facts and rules together.
    program = [f.strip() for f in facts if f.strip()] + \
              [r.strip() for r in rules if r.strip()]
    if not program:
        raise ToolError("empty knowledge base (no facts or rules)")
    try:
        kb(program)
    except Exception as e:
        raise ToolError(f"KB compile error: {type(e).__name__}: {e}") from e
    return kb


def _run_query_sync(kb, query_str: str) -> tuple[list[dict], str]:
    """Execute a single query.

    Returns (bindings, summary):
      - bindings: list of {var:value} dicts (empty for ground or no-match)
      - summary: 'Yes' / 'No' for ground queries; 'N solution(s)' otherwise
    """
    import pytholog as pl
    try:
        result = kb.query(pl.Expr(query_str.strip()))
    except Exception as e:
        raise ToolError(f"query error: {type(e).__name__}: {e}") from e

    # pytholog returns a LIST in all cases:
    #   - [{'X': 'val'}, ...] for variable queries
    #   - ['Yes'] for ground match
    #   - ['No'] for no match
    # Normalise to (list-of-dicts, summary-string).
    if not isinstance(result, list):
        # Defensive: future pytholog versions might return scalars
        return ([], str(result))
    if not result:
        return ([], "No")
    # Single-element string result is ground YES / NO
    if len(result) == 1 and isinstance(result[0], str):
        return ([], result[0])
    # Variable bindings — list of dicts. Cap at 100 to avoid blowing
    # the token budget on cyclic rules.
    bindings = [b for b in result if isinstance(b, dict)][:100]
    return (bindings, f"{len(bindings)} solution(s)" if bindings else "No")


# ── Dispatch ───────────────────────────────────────────────────────────

async def _op_query(args: PrologArgs) -> PrologResult:
    if not args.query.strip():
        raise ToolError("`query` is empty")
    _validate_kb(args.facts, args.rules)
    kb = _build_kb(args.facts, args.rules)

    async def _go():
        return _run_query_sync(kb, args.query)

    try:
        bindings, summary = await asyncio.wait_for(_go(), timeout=args.timeout_sec)
    except asyncio.TimeoutError as e:
        raise ToolError(
            f"query timeout after {args.timeout_sec}s — narrow the "
            f"query, add more constraints, or check for cyclic rules"
        ) from e

    return PrologResult(
        ok=True, op="query", bindings=bindings, summary=summary,
        kb_size=len(args.facts) + len(args.rules),
    )


async def _op_consult(args: PrologArgs) -> PrologResult:
    _validate_kb(args.facts, args.rules)
    _build_kb(args.facts, args.rules)
    return PrologResult(
        ok=True, op="consult", summary="ok",
        kb_size=len(args.facts) + len(args.rules),
    )


async def _op_assert_and_query(args: PrologArgs) -> PrologResult:
    r = await _op_query(args)
    r.op = "assert_and_query"
    return r


_DISPATCH = {
    "query":            _op_query,
    "consult":          _op_consult,
    "assert_and_query": _op_assert_and_query,
}


class Prolog(
    BaseTool[PrologArgs, PrologResult, PrologConfig, BaseToolState],
    ToolUIData[PrologArgs, PrologResult],
):
    description: ClassVar[str] = (
        "Logic-programming via pytholog (pure-Python Prolog). When "
        "you have facts + rules and need to query relations, use this "
        "INSTEAD of trying to reason about transitive / recursive "
        "relations by hand. Strengths: family/relation puzzles, "
        "ancestor / path-finding, all-solutions enumeration, "
        "horn-clause inference. Operations: query, consult, "
        "assert_and_query. Standard Prolog syntax — lowercase atoms, "
        "UPPERCASE variables, `name(arg1, arg2)` predicates, "
        "`head(X) :- body1(X), body2(X)` rules. For arithmetic / "
        "constraint problems use `solve` (Z3) instead — Prolog is "
        "for symbolic logic and relations."
    )

    @classmethod
    def format_call_display(cls, args: PrologArgs) -> ToolCallDisplay:
        q = args.query.strip()[:40] or "(consult)"
        return ToolCallDisplay(
            summary=f"prolog[{args.op}]: {len(args.facts)}f+{len(args.rules)}r → {q}"
        )

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, PrologResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"prolog: {event.result.error[:80]}"
                )
            return ToolResultDisplay(
                success=True,
                message=f"{event.result.summary}",
            )
        return ToolResultDisplay(success=True, message="prolog complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Reasoning with Prolog"

    def resolve_permission(self, args: PrologArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: PrologArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | PrologResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield PrologResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            yield await handler(args)
        except ToolError as e:
            yield PrologResult(ok=False, op=args.op, error=str(e))
        except Exception as e:  # noqa: BLE001 — surface to model, don't crash
            yield PrologResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
