"""Modular Context Runtime (MCR) — Phase 1: addressable context objects + store.

See docs/modular_context_runtime_prd.md: §5 (address space), §6 (residency classes),
§18 (scopes / promotion), §28 phase 1.

Phase 1 introduces the OBJECTS and their PERSISTENCE only. Paging/working-set
construction (§7), the tombstone machinery (§6), multi-resolution bodies (§14) and
the Context Scheduler (§19) are later phases and are deliberately absent here — the
point of this slice is a durable, addressable context space that nothing else depends
on yet, so it can land without touching the agent loop.

Idioms follow the rest of Drydock: stdlib-only, append-only JSONL with last-write-wins
per id (the swarm Blackboard pattern), and swallow-all-errors I/O — context bookkeeping
must never break a run.

Token sizing reuses ``drydock.compaction.estimate_tokens`` on purpose: if MCR sized
modules differently from the compactor, the pager and the compactor would disagree
about how full the context is and fight each other.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

CTX_SCHEME = "ctx://"

# ── §6 residency classes ──────────────────────────────────────────────────────
PINNED = "pinned"           # always resident (system, objective, constraints)
WORKING = "working"         # needed for the current operation
SHARED = "shared"           # reusable across executions (repo architecture, conventions)
ARCHIVED = "archived"       # full text retained OUT of context; can be paged back in
TOMBSTONED = "tombstoned"   # compact record of something deliberately dropped
RESIDENCY = (PINNED, WORKING, SHARED, ARCHIVED, TOMBSTONED)

# ── §18 scopes, in promotion order ────────────────────────────────────────────
PRIVATE = "private"
BRANCH = "branch"
TASK = "task"
PROJECT = "project"
GLOBAL = "global"
SCOPES = (PRIVATE, BRANCH, TASK, PROJECT, GLOBAL)

# ctx://<namespace>/<path> — namespace and path segments are [a-z0-9._-]
_CTX_RE = re.compile(r"^ctx://[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)*$")


def valid_context_id(cid: str) -> bool:
    """A context id is an addressable ctx:// URI (§5)."""
    return bool(cid) and isinstance(cid, str) and bool(_CTX_RE.match(cid))


def estimate_body_tokens(body: str) -> int:
    """Size a module's body with the SAME estimator the compactor uses, so budgets
    agree across the two systems."""
    try:
        from drydock.compaction import estimate_tokens
        return estimate_tokens([{"content": body or ""}])
    except Exception:  # noqa: BLE001 — sizing must never break bookkeeping
        return int(len(body or "") / 3.0)


@dataclass
class ContextModule:
    """One addressable unit of context (§5). `body` is the full text; residency says
    whether it is currently meant to occupy the model's context, NOT whether it is
    stored — storage is lossless, residency is selective (§2)."""
    context_id: str
    body: str = ""
    type: str = ""                                   # hypothesis|decision|failure|repo|tool|task|system
    residency: str = WORKING
    scope: str = PRIVATE
    owner: str = ""
    created_from: Optional[str] = None               # lineage (§5 created_from)
    dependencies: list = field(default_factory=list)  # other context_ids (§5)
    verified: bool = False
    priority: float = 0.0
    version: int = 0
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        if not valid_context_id(self.context_id):
            raise ValueError(f"invalid context id (want ctx://ns/path): {self.context_id!r}")
        if self.residency not in RESIDENCY:
            raise ValueError(f"unknown residency: {self.residency!r}")
        if self.scope not in SCOPES:
            raise ValueError(f"unknown scope: {self.scope!r}")

    @property
    def token_size(self) -> int:
        return estimate_body_tokens(self.body)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["token_size"] = self.token_size      # materialised so readers needn't recompute
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ContextModule":
        known = {f for f in cls.__dataclass_fields__}           # noqa: SLF001 — dataclass API
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


def _promotable(frm: str, to: str, verified: bool) -> bool:
    """§18: promotion moves UP the ladder only, and anything above BRANCH requires
    evidence. This is what stops a speculative agent's hallucination from becoming
    durable PROJECT knowledge."""
    if frm not in SCOPES or to not in SCOPES:
        return False
    if SCOPES.index(to) <= SCOPES.index(frm):
        return False
    if SCOPES.index(to) > SCOPES.index(BRANCH) and not verified:
        return False
    return True


class ContextStore:
    """Persistent, addressable context space: append-only JSONL, last-write-wins per
    context_id. Lossless — superseded versions stay in the log, so nothing is destroyed
    by a residency change (§2)."""

    def __init__(self, root: str = ".", name: str = "default"):
        self.name = name
        self.dir = Path(root) / ".drydock" / "context" / name
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.path = self.dir / "modules.jsonl"

    # ── write ────────────────────────────────────────────────────────────────
    def put(self, module: ContextModule) -> ContextModule:
        """Append a new version of a module. Returns the stored module (version bumped)."""
        prev = self.get(module.context_id)
        module.version = (prev.version + 1) if prev else 1
        module.ts = time.time()
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(module.to_dict(), default=str, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            pass
        return module

    def set_residency(self, context_id: str, residency: str) -> Optional[ContextModule]:
        """Move a module between residency classes WITHOUT losing its body (§2)."""
        m = self.get(context_id)
        if m is None or residency not in RESIDENCY:
            return None
        m.residency = residency
        return self.put(m)

    def promote(self, context_id: str, to_scope: str) -> Optional[ContextModule]:
        """Raise a module's scope per the §18 ladder. Returns None (no write) when the
        promotion is not allowed — e.g. unverified knowledge reaching for PROJECT."""
        m = self.get(context_id)
        if m is None or not _promotable(m.scope, to_scope, m.verified):
            return None
        m.scope = to_scope
        return self.put(m)

    # ── read ─────────────────────────────────────────────────────────────────
    def _rows(self) -> list:
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

    def all(self) -> list:
        """Current version of every module, insertion-ordered by first appearance."""
        latest: dict = {}
        for r in self._rows():
            cid = r.get("context_id")
            if cid:
                latest[cid] = r
        return [ContextModule.from_dict(r) for r in latest.values()]

    def get(self, context_id: str) -> Optional[ContextModule]:
        found = None
        for r in self._rows():
            if r.get("context_id") == context_id:
                found = r
        return ContextModule.from_dict(found) if found else None

    def by_residency(self, *residency: str) -> list:
        want = set(residency)
        return [m for m in self.all() if m.residency in want]

    def by_scope(self, *scope: str) -> list:
        want = set(scope)
        return [m for m in self.all() if m.scope in want]

    def resolve(self, context_id: str) -> list:
        """A module plus its transitive `dependencies` (§5 references), cycle-safe,
        dependencies first. This is what a future working-set builder mounts together."""
        seen: set = set()
        order: list = []

        def walk(cid: str) -> None:
            if cid in seen:
                return
            seen.add(cid)
            m = self.get(cid)
            if m is None:
                return
            for dep in m.dependencies or []:
                walk(dep)
            order.append(m)

        walk(context_id)
        return order

    def total_tokens(self, *residency: str) -> int:
        mods = self.by_residency(*residency) if residency else self.all()
        return sum(m.token_size for m in mods)

    # ── §28 phase 2: explicit mount / unmount between inference calls ─────────
    def mount(self, context_id: str) -> Optional[ContextModule]:
        """Make a module part of the current working set."""
        return self.set_residency(context_id, WORKING)

    def unmount(self, context_id: str) -> Optional[ContextModule]:
        """Evict from the context window — the body is RETAINED (§2), so this is a
        residency change, not a deletion; it can be paged back in with mount()."""
        return self.set_residency(context_id, ARCHIVED)

    def pin(self, context_id: str) -> Optional[ContextModule]:
        return self.set_residency(context_id, PINNED)


# ── §7 working set / Context View ────────────────────────────────────────────

# Prompt order, most-stable first. This is NOT cosmetic: measured on an idle vLLM
# box (research/mcr/prefix_cache_probe.py, PRD Appendix A.1), re-prefill cost scales
# with how EARLY in the prompt a change lands — tail 0.24x, middle 0.59x, head 0.98x
# of a cold prefill, i.e. a head mutation costs ~4.2x a tail mutation. Ordering by
# how often a class changes keeps mutations in the TAIL so the cached prefix survives.
VIEW_ORDER = (PINNED, SHARED, TOMBSTONED, WORKING)


@dataclass
class ContextView:
    """The concrete working set handed to one inference call (§7/§8). `modules` is in
    prompt order; `evicted` are ids that did not fit the budget."""
    modules: list = field(default_factory=list)
    budget: int = 0
    evicted: list = field(default_factory=list)
    order: tuple = VIEW_ORDER

    @property
    def total_tokens(self) -> int:
        return sum(m.token_size for m in self.modules)

    @property
    def over_budget(self) -> bool:
        return self.total_tokens > self.budget

    @property
    def stable_prefix_tokens(self) -> int:
        """Tokens in the classes that should NOT change between turns (pinned+shared).
        This is the span whose KV cache we are trying to preserve."""
        return sum(m.token_size for m in self.modules
                   if m.residency in (PINNED, SHARED))

    def render(self) -> str:
        return "\n\n".join(m.body for m in self.modules if m.body)

    def ids(self) -> list:
        return [m.context_id for m in self.modules]


def build_view(store: ContextStore, budget: int, *,
               include: "list | None" = None,
               order: tuple = VIEW_ORDER) -> ContextView:
    """Assemble a Context View under a token budget (§7: Tokens(W_t) <= B).

    SELECTION and ORDERING are deliberately separate concerns:
      * selection — what earns a place, by `priority` (highest first), fitting the
        budget. PINNED is exempt: it is always resident by definition (§6).
      * ordering  — the surviving modules are then laid out most-stable-first
        (VIEW_ORDER), so that turn-to-turn changes land in the prompt TAIL and the
        cached prefix survives (Appendix A.1, measured).

    `include` optionally restricts consideration to specific ids (pinned modules are
    always included regardless). ARCHIVED/unknown residencies are never mounted.
    """
    mods = [m for m in store.all() if m.residency in order]
    if include is not None:
        want = set(include)
        mods = [m for m in mods if m.context_id in want or m.residency == PINNED]

    pinned = [m for m in mods if m.residency == PINNED]
    rest = [m for m in mods if m.residency != PINNED]
    # selection: priority desc, then cheaper first so a big low-value module cannot
    # crowd out several small useful ones
    rest.sort(key=lambda m: (-m.priority, m.token_size))

    used = sum(m.token_size for m in pinned)
    kept: list = []
    evicted: list = []
    for m in rest:
        if used + m.token_size <= budget:
            kept.append(m)
            used += m.token_size
        else:
            evicted.append(m.context_id)

    selected = pinned + kept
    # ordering: stable sort keeps the priority order within each residency class
    selected.sort(key=lambda m: order.index(m.residency))
    return ContextView(modules=selected, budget=budget, evicted=evicted, order=order)


def prefix_reuse(previous: "ContextView | None", current: ContextView) -> dict:
    """How much of `previous`'s prompt prefix `current` can reuse from the KV cache.

    Walks both views in prompt order and stops at the first module that differs by id
    OR by version (a same-id module whose body changed invalidates from there on, just
    like a replacement). Returns reused/reprefill token counts — the cheap, offline
    predictor of what Appendix A.1 measured, so a scheduler can see the cost of a
    mounting decision BEFORE paying it.
    """
    cur = current.modules
    total = sum(m.token_size for m in cur)
    if not previous or not previous.modules:
        return {"reused_tokens": 0, "reprefill_tokens": total,
                "reuse_pct": 0.0, "diverged_at": 0}
    prev = previous.modules
    reused = 0
    idx = 0
    for idx in range(min(len(prev), len(cur))):
        a, b = prev[idx], cur[idx]
        if a.context_id != b.context_id or a.version != b.version:
            break
        reused += b.token_size
    else:
        idx = min(len(prev), len(cur))          # no divergence within the common span
    return {
        "reused_tokens": reused,
        "reprefill_tokens": total - reused,
        "reuse_pct": round(100.0 * reused / total, 1) if total else 0.0,
        "diverged_at": idx,
    }
