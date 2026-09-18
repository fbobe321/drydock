"""Modular Context Runtime (MCR) — addressable context objects, paging, and the
context ratchet.

See docs/modular_context_runtime_prd.md. Implemented here:
  * §5/§6/§18  — ctx:// address space, residency classes, scope-promotion ladder
  * §7/§28-2   — working-set construction under a token budget (build_view)
  * §10/§11/§12— ContextCheckpoint beside GitCheckpoint, the knowledge invariant
                 (commit_knowledge / Conflict), transactional context changes
  * §6/§28-4   — tombstones: retire a dead branch to a compact record, losslessly
  * §14        — multi-resolution modules, so the view can DEGRADE instead of evict

Still to come: the automatic Context Scheduler (§19/§28-6), context forks (§28-7) and
the unified compute/context governor (§28-8). Nothing here touches the agent loop yet.

ORDERING IS LOAD-BEARING, not cosmetic: re-prefill cost was measured to scale with how
EARLY in the prompt a change lands (Appendix A.1 — tail 0.24x, head 0.98x of a cold
prefill). VIEW_ORDER keeps mutations in the tail; prefix_reuse() prices a change before
it is paid for.

Idioms follow the rest of Drydock: stdlib-only, append-only JSONL with last-write-wins
per id (the swarm Blackboard pattern), and swallow-all-errors I/O — context bookkeeping
must never break a run.

Token sizing reuses ``drydock.compaction.estimate_tokens`` on purpose: if MCR sized
modules differently from the compactor, the pager and the compactor would disagree
about how full the context is and fight each other.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
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
    # Two DIFFERENT strengths of claim (Appendix A.4 — the verifier is corruptible):
    #   verifier_passed — "the verifier command reported success". Weak: a live run
    #                     produced `class _AlwaysEq: __eq__ -> True` and scored 10/10.
    #   verified        — independently corroborated. Only this unlocks §18 promotion
    #                     above BRANCH; see corroborate().
    verified: bool = False
    verifier_passed: bool = False
    corroborated_by: str = ""            # what justified `verified` (holdout, human, …)
    priority: float = 0.0
    version: int = 0
    ts: float = field(default_factory=time.time)
    # §14 multi-resolution: {level -> text}, higher level = more detail
    # (L0 pointer ~20 tok · L1 summary ~200 · L2 detailed ~1.5k · L3 evidence ~8k · L4 full)
    resolutions: dict = field(default_factory=dict)
    level: Optional[int] = None          # currently selected level; None = plain `body`

    def __post_init__(self):
        if not valid_context_id(self.context_id):
            raise ValueError(f"invalid context id (want ctx://ns/path): {self.context_id!r}")
        if self.residency not in RESIDENCY:
            raise ValueError(f"unknown residency: {self.residency!r}")
        if self.scope not in SCOPES:
            raise ValueError(f"unknown scope: {self.scope!r}")
        # JSON round-trips dict keys to strings; normalise so int and str both work
        if self.resolutions:
            self.resolutions = {str(k): v for k, v in self.resolutions.items()}
            if self.level is None:
                self.level = self.available_levels()[-1]      # default to most detailed
        if self.level is not None:
            self.level = int(self.level)

    # ── §14 resolution accessors ─────────────────────────────────────────────
    def available_levels(self) -> list:
        out = []
        for k in self.resolutions:
            try:
                out.append(int(k))
            except (TypeError, ValueError):
                continue
        return sorted(out)

    def text_at(self, level: "int | None") -> str:
        """Text at `level`, degrading gracefully: exact match, else the nearest
        available level BELOW it (never silently upgrade to something bigger than
        asked for), else the smallest available, else the plain body."""
        if not self.resolutions or level is None:
            return self.body
        levels = self.available_levels()
        if not levels:
            return self.body
        exact = self.resolutions.get(str(level))
        if exact is not None:
            return exact
        lower = [x for x in levels if x < level]
        pick = lower[-1] if lower else levels[0]
        return self.resolutions.get(str(pick), self.body)

    @property
    def current_text(self) -> str:
        """The representation this module currently contributes to a Context View."""
        return self.text_at(self.level) if self.resolutions else self.body

    def at_level(self, level: int) -> "ContextModule":
        """A copy of this module rendered at `level` — used to price/insert a degraded
        representation without mutating the stored module."""
        d = asdict(self)
        d["level"] = level
        m = ContextModule(**d)
        m.version = self.version
        return m

    @property
    def fingerprint(self) -> str:
        """Deterministic hash of the SERIALIZED representation (cache-aware spec §8).

        Cache identity must follow the bytes that reach the model, not the semantic id:
        two modules can share an id/version and serialize differently, or differ
        semantically and serialize identically. We hash the exact text rather than the
        token ids because Drydock must not depend on a tokenizer here — and for a
        deterministic tokenizer identical text implies identical tokens, which is the
        direction we need. (Different text that happens to tokenize identically is
        merely reported as a divergence we could have reused: conservative, never
        optimistic.)"""
        return hashlib.sha256(self.current_text.encode("utf-8", "replace")).hexdigest()[:16]

    @property
    def token_size(self) -> int:
        return estimate_body_tokens(self.current_text)

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
    durable PROJECT knowledge.

    NOTE the gate is `verified`, never `verifier_passed` (Appendix A.4): a passing
    verifier is not corroboration, because a passing verifier is exactly what a
    reward-hacked patch manufactures."""
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

    def record_verifier_pass(self, context_id: str, passed: bool = True) -> Optional[ContextModule]:
        """Record what the VERIFIER said — the weak claim. Deliberately does not touch
        `verified`, so a passing verifier alone can never buy promotion (Appendix A.4)."""
        m = self.get(context_id)
        if m is None:
            return None
        m.verifier_passed = bool(passed)
        return self.put(m)

    def corroborate(self, context_id: str, by: str) -> Optional[ContextModule]:
        """Promote the weak claim to the strong one: something INDEPENDENT of the
        verifier under test agreed (a holdout command the agent never saw, a human, a
        second differing implementation). `by` records what did the corroborating, so a
        later reader can judge whether it was worth anything. This is the only route to
        `verified`, and therefore the only route past BRANCH scope."""
        m = self.get(context_id)
        if m is None or not by:
            return None
        m.verified = True
        m.corroborated_by = by
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

    def get_version(self, context_id: str, version: int) -> Optional[ContextModule]:
        """A specific historical version. The log is append-only, so every superseded
        state is still recoverable — this is what makes context rollback possible."""
        for r in self._rows():
            if r.get("context_id") == context_id and r.get("version") == version:
                return ContextModule.from_dict(r)
        return None

    def versions(self) -> dict:
        """context_id -> current version, for every live module."""
        return {m.context_id: m.version for m in self.all()}

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
    degraded: dict = field(default_factory=dict)   # context_id -> level it was dropped to (§14)

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
        return "\n\n".join(m.current_text for m in self.modules if m.current_text)

    def ids(self) -> list:
        return [m.context_id for m in self.modules]

    def cache_class(self, m: "ContextModule") -> str:
        """Coarse stability label for the manifest (spec §7). Derived from residency,
        which is what VIEW_ORDER already sorts on."""
        return {PINNED: "immutable", SHARED: "stable", TOMBSTONED: "checkpoint",
                WORKING: "volatile"}.get(m.residency, "pageable")

    def manifest(self) -> dict:
        """The per-request context manifest (spec §7) — also the telemetry record."""
        return {
            "modules": [
                {"id": m.context_id, "version": m.version, "level": m.level,
                 "tokens": m.token_size, "fingerprint": m.fingerprint,
                 "cache_class": self.cache_class(m)}
                for m in self.modules
            ],
            "token_count": self.total_tokens,
            "stable_prefix_tokens": self.stable_prefix_tokens,
            "budget": self.budget,
            "evicted": list(self.evicted),
            "degraded": dict(self.degraded),
        }

    def prefix_fingerprints(self) -> list:
        """Cumulative prefix hashes P_i = H(M_1 || … || M_i) (spec §9). Two views share
        reusable prefix state up to the last index where these agree."""
        out, acc = [], hashlib.sha256()
        for m in self.modules:
            acc.update(m.fingerprint.encode("ascii"))
            out.append(acc.hexdigest()[:16])
        return out


def build_view(store: ContextStore, budget: int, *,
               include: "list | None" = None,
               order: tuple = VIEW_ORDER,
               degrade: bool = True) -> ContextView:
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
    degraded: dict = {}
    for m in rest:
        # §14 memory hierarchy: prefer DEGRADING a module to a cheaper representation
        # over dropping it entirely — a 20-token pointer still tells the model the
        # thing exists and can be paged back up, where an eviction tells it nothing.
        placed = False
        candidates = [m]
        if degrade and m.resolutions and m.level is not None:
            candidates += [m.at_level(lvl)
                           for lvl in reversed(m.available_levels()) if lvl < m.level]
        for cand in candidates:
            if used + cand.token_size <= budget:
                kept.append(cand)
                used += cand.token_size
                if cand.level != m.level:
                    degraded[m.context_id] = cand.level
                placed = True
                break
        if not placed:
            evicted.append(m.context_id)

    selected = pinned + kept
    # ordering: stable sort keeps the priority order within each residency class
    selected.sort(key=lambda m: order.index(m.residency))
    return ContextView(modules=selected, budget=budget, evicted=evicted, order=order,
                       degraded=degraded)


# ── §6 / §28 phase 4: tombstones ─────────────────────────────────────────────

@dataclass
class Tombstone:
    """The compact record left behind when a line of work is abandoned (§6). It exists
    so the model stops paying the token cost of a dead branch WITHOUT losing the fact
    that the branch was tried and why.

    `evidence` is load-bearing (Appendix A.3): a tombstone is a model-authored claim
    that something failed, and a wrong one durably suppresses an approach that would
    have worked. A tombstone with verifier evidence is marked verified and may be
    promoted up the §18 ladder; one without stays unverified and therefore cannot pass
    BRANCH scope — it can guide the branch that made it, never the whole project.
    """
    approach: str
    result: str = ""
    reason: str = ""
    revisit_if: str = ""
    evidence: dict = field(default_factory=dict)   # e.g. {"tests_failed": [14, 17]}
    source: str = ""                               # ctx:// id of the full archived trace

    def render(self) -> str:
        lines = [f"Approach: {self.approach}"]
        if self.result:
            lines.append(f"Result: {self.result}")
        if self.reason:
            lines.append(f"Reason: {self.reason}")
        if self.evidence:
            lines.append(f"Evidence: {json.dumps(self.evidence, sort_keys=True, default=str)}")
        lines.append(f"Revisit only if: {self.revisit_if or 'new evidence appears'}")
        if self.source:
            lines.append(f"Source: {self.source}")
        return "\n".join(lines)


def tombstone_id(context_id: str) -> str:
    """ctx://branch/parser-fix-03 -> ctx://tombstone/branch.parser-fix-03"""
    return "ctx://tombstone/" + context_id[len(CTX_SCHEME):].replace("/", ".")


def tombstone(store: ContextStore, context_id: str, *, approach: str,
              result: str = "", reason: str = "", revisit_if: str = "",
              evidence: "dict | None" = None) -> Optional[ContextModule]:
    """Retire a module: archive the FULL body and leave a compact tombstone in its place.

    Returns the new tombstone module, or None if `context_id` is unknown. The original
    is only moved to ARCHIVED — never deleted — so the complete trace stays retrievable
    (§2, §13: "Dead A 24K -> 300-token tombstone", with the 24K still on disk).

    On `verified` and Appendix A.4: evidence here is a claim that something FAILED, and
    that is asymmetric with a claim that something PASSED. Reward hacking manufactures
    passes — the incentive to fabricate a *failure* report is absent — so a tombstone
    naming the checks the verifier saw fail is treated as corroborating evidence, while
    a passing verifier is only ever `verifier_passed`. An unevidenced tombstone remains
    unverified and cannot pass BRANCH, per §18/A.3.
    """
    original = store.get(context_id)
    if original is None:
        return None
    store.set_residency(context_id, ARCHIVED)
    ts = Tombstone(approach=approach, result=result, reason=reason,
                   revisit_if=revisit_if, evidence=dict(evidence or {}),
                   source=context_id)
    return store.put(ContextModule(
        context_id=tombstone_id(context_id),
        body=ts.render(),
        type="failure",
        residency=TOMBSTONED,
        scope=original.scope,               # inherits, then must EARN promotion (§18)
        owner=original.owner,
        created_from=context_id,
        dependencies=[context_id],
        verified=bool(evidence),            # unevidenced claims cannot pass BRANCH
    ))


def revive(store: ContextStore, context_id: str) -> Optional[ContextModule]:
    """Undo a tombstone: page the original body back into the working set and retire
    the tombstone itself.

    This is the safety valve for Appendix A.3 — tombstones are model-authored and can
    be wrong, so "this was ruled out" must always be reversible. Accepts either the
    tombstone's id or the original's id.
    """
    m = store.get(context_id)
    if m is None:
        return None
    if m.residency == TOMBSTONED:
        original_id = m.created_from or ""
        store.set_residency(m.context_id, ARCHIVED)     # stop suppressing; keep the record
    else:
        original_id = context_id
        t = store.get(tombstone_id(context_id))
        if t is not None and t.residency == TOMBSTONED:
            store.set_residency(t.context_id, ARCHIVED)
    return store.mount(original_id) if original_id else None


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
        # Compare ONLY what the model actually sees (spec §8/§9). Not the id: two
        # different modules with identical text render identical prompt bytes and the
        # server WILL reuse that prefix, so keying on semantic identity would
        # under-report reuse just as surely as it over-reported it before.
        if a.fingerprint != b.fingerprint:
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


# ── §10/§11/§12 — Ratchet integration: context checkpoints & the knowledge invariant ──

@dataclass
class Conflict:
    """A newly committed claim contradicts previously VERIFIED knowledge (§12).

    Recorded rather than resolved: the old module is left standing and a conflict
    module is mounted so the disagreement is visible and must be reconciled by
    evidence — the opposite of a silent overwrite.
    """
    context_id: str
    existing_body: str
    incoming_body: str
    reason: str = "incoming claim contradicts verified knowledge"

    def render(self) -> str:
        return ("CONFLICT — verified knowledge contradicted; reconcile by verification.\n"
                f"Module: {self.context_id}\n"
                f"Reason: {self.reason}\n"
                f"OLD (verified): {self.existing_body}\n"
                f"NEW (observed): {self.incoming_body}")


def conflict_id(context_id: str) -> str:
    return "ctx://conflict/" + context_id[len(CTX_SCHEME):].replace("/", ".")


def commit_knowledge(store: ContextStore, module: ContextModule,
                     *, resolve: bool = False) -> "tuple[Optional[ContextModule], Optional[Conflict]]":
    """Commit knowledge under the §12 context-ratchet invariant.

    For code the ratchet guarantees F(t+1) >= F(t). The knowledge equivalent is weaker
    but sharper: *newly committed knowledge must not silently invalidate previously
    verified knowledge*. So overwriting a VERIFIED module with a different body does
    not win by being newer — it raises a Conflict, mounts it for reconciliation, and
    leaves the old module intact. Pass resolve=True once evidence settles it.

    Returns (stored_module, conflict); exactly one is non-None.
    """
    existing = store.get(module.context_id)
    contradicts = (existing is not None and existing.verified
                   and existing.body != module.body)
    if contradicts and not resolve:
        c = Conflict(context_id=module.context_id,
                     existing_body=existing.body, incoming_body=module.body)
        store.put(ContextModule(
            context_id=conflict_id(module.context_id),
            body=c.render(), type="conflict", residency=WORKING,
            scope=existing.scope, created_from=module.context_id,
            dependencies=[module.context_id], verified=False,
        ))
        return None, c
    return store.put(module), None


class ContextCheckpoint:
    """Snapshot/restore of the whole context store, mirroring ratchet.GitCheckpoint's
    shape (available/snapshot/restore) so a Ratchet tooth can checkpoint CODE and
    KNOWLEDGE together (§11).

    A snapshot is just the {context_id: version} map — cheap, because the store is
    append-only and every superseded version is still on disk. Restore re-appends the
    snapshot's content as new versions (never rewriting history) and archives modules
    created after the snapshot, so rollback is itself lossless (§2).
    """

    def __init__(self, store: ContextStore):
        self.store = store
        self.path = store.dir / "checkpoints.jsonl"

    def available(self) -> bool:
        return self.store.dir.exists()

    def _all(self) -> list:
        out: list = []
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
        return out

    def snapshot(self, label: str = "", *, git_ref: str = "",
                 fitness: "float | None" = None) -> str:
        """Record the current context state. `git_ref`/`fitness` tie this tooth to the
        repo snapshot and verifier score that justified it (§11)."""
        rec = {
            "id": f"ckpt-{len(self._all()) + 1:04d}",
            "label": label,
            "git_ref": git_ref,
            "fitness": fitness,
            "versions": self.store.versions(),
            "ts": time.time(),
        }
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            pass
        return rec["id"]

    def get(self, checkpoint_id: str) -> Optional[dict]:
        for r in self._all():
            if r.get("id") == checkpoint_id:
                return r
        return None

    def restore(self, checkpoint_id: str) -> bool:
        """Roll the context store back to a checkpoint. Modules created since are
        ARCHIVED (not deleted); modules changed since are re-appended at their
        checkpoint content."""
        rec = self.get(checkpoint_id)
        if rec is None:
            return False
        want = {k: int(v) for k, v in (rec.get("versions") or {}).items()}
        for m in self.store.all():
            cid = m.context_id
            if cid not in want:
                self.store.set_residency(cid, ARCHIVED)      # born after the checkpoint
                continue
            if m.version != want[cid]:
                old = self.store.get_version(cid, want[cid])
                if old is not None:
                    self.store.put(old)                      # re-append old content as a new version
        return True


class _Txn:
    """Handle yielded by context_transaction()."""

    def __init__(self, checkpoint_id: str):
        self.checkpoint_id = checkpoint_id
        self.committed = False

    def commit(self) -> None:
        self.committed = True


@contextmanager
def context_transaction(store: ContextStore, label: str = ""):
    """Transactional context change (§10): BEGIN -> work -> verify -> COMMIT, else
    ROLLBACK. A speculative branch cannot silently contaminate shared knowledge — if
    the block raises, or exits without commit(), the store is rolled back to the
    checkpoint taken on entry.

        with context_transaction(store, "try regex parser") as tx:
            ...explore, write modules...
            if verifier_improved:
                tx.commit()
        # no commit -> rolled back; tombstone the attempt if it is worth remembering
    """
    ckpt = ContextCheckpoint(store)
    cid = ckpt.snapshot(label=label or "txn")
    tx = _Txn(cid)
    try:
        yield tx
    except Exception:
        ckpt.restore(cid)
        raise
    if not tx.committed:
        ckpt.restore(cid)
