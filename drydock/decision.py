"""Laya System-1 Decision Plane — a fast, model-independent decision service for the
frequent low-cost orchestration choices Drydock currently spends a generative LLM on
(docs/laya_prd.md). "Laya decides. Generative models work. Ratchet measures. Handshake
orchestrates."

The load-bearing idea is the INTERFACE, not the model. Handshake/ABC depend on
`DecisionProvider`; whether the answer comes from Laya, a heuristic, or the primary LLM is a
config choice (§6, §19). That keeps Drydock from coupling to one experimental model — swap
`provider: laya` for `provider: heuristic` and nothing else changes.

MVP scope (§22 Phase 1): the interface, the compact `ControlState`, a `HeuristicProvider`
(always available — the §18 fallback and a genuinely useful default), a `LayaProvider` HTTP
client (transport injected so it is testable without a live server), a confidence router
(§16), a fallback chain (§18), and decision logging (§20). Three decisions are wired:
CONTINUE?, WHAT NEXT?, HOW MUCH REASONING? — plus `limiting_resource` for the ABC coupling
(§11). Later phases add context/agent routing.

Deviation from the PRD sketch: the interface is SYNC, not async. Drydock's provider layer is
sync and these decisions are fast/local; a sync interface is testable without an event loop
and matches capacity.py's injectable-transport style. Revisit if a remote Laya needs
concurrency.

All logic original to Drydock. Providers never raise for a *decision* — an unreachable Laya
raises `DecisionUnavailable` so the chain falls back; a bad answer degrades to abstention.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

# ---- confidence thresholds (§16). A decision at/above `execute` is acted on; between
#      `fallback` and `execute` it is blended with heuristics; below `fallback` it escalates.
EXECUTE_THRESHOLD = 0.80
FALLBACK_THRESHOLD = 0.55


class DecisionUnavailable(RuntimeError):
    """A provider could not produce a decision (server down, malformed reply). The fallback
    chain catches this and tries the next provider (§18); it is never fatal to a task."""


# ======================================================================================
# Control state (§7/§8) — the compact "cognitive dashboard", NOT the working context.
# ======================================================================================
@dataclass
class ControlState:
    """A small, structured snapshot the decision plane reasons over (~200-500 tokens, §8).
    Deliberately NOT the repo or the transcript: the decision model needs the shape of the
    situation, not its full content."""
    objective: str = ""
    task_type: str = "coding"
    fitness: int = 0
    maximum: int = 0
    previous_fitness: int = 0
    plateau_iterations: int = 0
    strategy_family: str = ""       # semantic fingerprint of the current approach (§15)
    family_attempts: int = 0        # attempts within this family with no verified gain
    last_action: str = ""
    active_modules: list[str] = field(default_factory=list)
    available_modules: list[str] = field(default_factory=list)
    reasoning: str = "medium"
    agents: int = 1
    iterations: int = 0
    available_actions: list[str] = field(default_factory=list)
    hypotheses: int = 1
    needs_external_info: bool = False

    @property
    def improved(self) -> bool:
        return self.fitness > self.previous_fitness

    @property
    def solved(self) -> bool:
        return self.maximum > 0 and self.fitness >= self.maximum

    @property
    def stalled(self) -> bool:
        return self.plateau_iterations >= 2 and not self.improved

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt(self) -> str:
        """Compact rendering for a decision model (§8). Terse on purpose — this is the whole
        point of the control/working context split."""
        mods = ",".join(self.active_modules) or "-"
        avail = ",".join(m for m in self.available_modules if m not in self.active_modules) or "-"
        return (
            f"objective: {self.objective}\n"
            f"fitness: {self.fitness}/{self.maximum} (prev {self.previous_fitness}); "
            f"plateau {self.plateau_iterations}\n"
            f"strategy: {self.strategy_family or '?'} ({self.family_attempts} tries, no gain)\n"
            f"last_action: {self.last_action or '-'}\n"
            f"context: active[{mods}] available[{avail}]\n"
            f"resources: reasoning={self.reasoning} agents={self.agents} iters={self.iterations}\n"
            f"actions: {','.join(self.available_actions) or '-'}"
        )


# ======================================================================================
# Decision result + interface (§6, §9).
# ======================================================================================
@dataclass
class Decision:
    """A probabilistic answer, not a command (§16). `selected` is the top option; callers
    apply the confidence thresholds before acting."""
    question: str
    options: dict[str, float] = field(default_factory=dict)
    selected: str = ""
    confidence: float = 0.0
    source: str = ""                # provider that produced it
    latency_ms: float = 0.0

    @classmethod
    def from_scores(cls, question: str, options: dict[str, float], *, source: str = "",
                    latency_ms: float = 0.0) -> "Decision":
        if not options:
            return cls(question=question, source=source, latency_ms=latency_ms)
        top = max(options, key=lambda k: options[k])
        return cls(question=question, options=dict(options), selected=top,
                   confidence=float(options[top]), source=source, latency_ms=latency_ms)

    @property
    def actionable(self) -> bool:
        return self.confidence >= EXECUTE_THRESHOLD

    @property
    def uncertain(self) -> bool:
        return self.confidence < FALLBACK_THRESHOLD

    def to_dict(self) -> dict:
        return asdict(self)


class DecisionProvider(Protocol):
    """The seam Handshake/ABC depend on (§6). An implementation answers three primitive
    shapes; the named decisions (continue?, next action, reasoning budget) are built on top,
    so a new provider only implements these."""
    name: str

    def boolean(self, state: ControlState, question: str) -> Decision: ...
    def choose(self, state: ControlState, question: str, choices: list[str]) -> Decision: ...
    def score(self, state: ControlState, criterion: str) -> float: ...


# ======================================================================================
# Heuristic provider (§18 fallback; always available, no model, fully deterministic).
# ======================================================================================
class HeuristicProvider:
    """Rule-of-thumb decisions from ControlState alone. This is the safety net when Laya is
    unavailable (§18) AND an honest baseline arm for the §23 experiment (arm B). Rules mirror
    the ABC ladder: a deepening plateau first wants information, then parallel search, then a
    strategy change — reasoning is escalated, not reflexively maxed."""
    name = "heuristic"

    def boolean(self, state: ControlState, question: str) -> Decision:
        t0 = time.monotonic()
        q = question.lower()
        if "continue" in q:
            # keep going while progress is fresh; lose confidence as the plateau deepens.
            p = 0.85 if state.improved else max(0.05, 0.5 - 0.2 * state.plateau_iterations)
            opts = {"yes": p, "no": round(1 - p, 3)}
        elif "complete" in q or "finish" in q or "done" in q:
            # Laya may recommend completion but the verifier is authoritative (§9/§17).
            p = 0.9 if state.solved else 0.05
            opts = {"yes": p, "no": round(1 - p, 3)}
        elif "external" in q or "information" in q:
            p = 0.8 if state.needs_external_info else 0.2
            opts = {"yes": p, "no": round(1 - p, 3)}
        else:
            opts = {"yes": 0.5, "no": 0.5}
        return Decision.from_scores(question, opts, source=self.name,
                                    latency_ms=(time.monotonic() - t0) * 1000)

    def choose(self, state: ControlState, question: str, choices: list[str]) -> Decision:
        t0 = time.monotonic()
        scores = {c: 0.1 for c in choices}
        q = question.lower()
        if "resource" in q or "limiting" in q:
            self._score_limiting_resource(state, scores)
        elif "action" in q or "next" in q:
            self._score_next_action(state, scores)
        elif "reason" in q or "budget" in q:
            self._score_reasoning(state, scores)
        elif "module" in q or "context" in q or "retrieve" in q:
            self._score_module(state, scores)
        total = sum(scores.values()) or 1.0
        norm = {c: round(v / total, 3) for c, v in scores.items()}
        return Decision.from_scores(question, norm, source=self.name,
                                    latency_ms=(time.monotonic() - t0) * 1000)

    def score(self, state: ControlState, criterion: str) -> float:
        c = criterion.lower()
        if "progress" in c:
            if state.solved:
                return 1.0
            if state.improved:
                return 0.7
            return max(0.0, 0.4 - 0.1 * state.plateau_iterations)
        return 0.5

    # -- internal scoring: bias toward the CHEAPEST axis that fits the symptom -----------
    def _score_limiting_resource(self, s: ControlState, scores: dict) -> None:
        # §11: a missing-info stall wants context; several open hypotheses want agents; a
        # repeated strategy family wants a different strategy; only an evidence-rich but
        # weak-conclusion stall wants more reasoning.
        if "more_context" in scores and (s.needs_external_info or _has_unloaded(s)):
            scores["more_context"] += 0.6
        if "more_agents" in scores and s.hypotheses > 1:
            scores["more_agents"] += 0.4
        if "different_strategy" in scores and s.family_attempts >= 3:
            scores["different_strategy"] += 0.6
        if "more_tools" in scores and s.task_type == "coding":
            scores["more_tools"] += 0.3
        if "more_reasoning" in scores and not (s.needs_external_info or _has_unloaded(s)):
            scores["more_reasoning"] += 0.2 + 0.1 * s.plateau_iterations

    def _score_next_action(self, s: ControlState, scores: dict) -> None:
        if s.solved and "finish" in scores:
            scores["finish"] += 0.8
        if "retrieve_context" in scores and _has_unloaded(s) and s.stalled:
            scores["retrieve_context"] += 0.6
        if "spawn_agents" in scores and s.hypotheses > 1 and s.stalled:
            scores["spawn_agents"] += 0.4
        if "change_strategy" in scores and s.family_attempts >= 3:
            scores["change_strategy"] += 0.6
        if "reason_deeper" in scores and s.stalled and not _has_unloaded(s):
            scores["reason_deeper"] += 0.3
        if "continue" in scores and not s.stalled:
            scores["continue"] += 0.7

    def _score_reasoning(self, s: ControlState, scores: dict) -> None:
        # deeper plateau -> more reasoning, but never jump straight to maximum.
        order = ["minimal", "low", "medium", "high", "maximum"]
        target = min(1 + s.plateau_iterations, len(order) - 2)  # cap at "high" by default
        for i, lvl in enumerate(order):
            if lvl in scores:
                scores[lvl] += max(0.05, 0.6 - 0.2 * abs(i - target))

    def _score_module(self, s: ControlState, scores: dict) -> None:
        # with no semantic signal the heuristic can't rank modules well; spread evenly and
        # let Laya (or the LLM) do the real routing. Slightly favour "previous_attempts".
        if "previous_attempts" in scores and s.family_attempts >= 2:
            scores["previous_attempts"] += 0.3


def _has_unloaded(s: ControlState) -> bool:
    return any(m not in s.active_modules for m in s.available_modules)


# ======================================================================================
# Laya provider (§5) — OpenAI-compatible decision endpoint; transport injected for testing.
# ======================================================================================
# A caller may pass its own transport: (url, payload_dict, timeout) -> reply_text. Defaults
# to a urllib POST, mirroring capacity.py / providers.py conventions (user's own server).
Transport = Callable[[str, dict, float], str]


def _default_transport(url: str, payload: dict, timeout: float) -> str:
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — user's own server
        return r.read().decode("utf-8")


class LayaProvider:
    """Client for a Laya decision service (§5). Sends the COMPACT ControlState and a question,
    expects a JSON object of option->probability. Any failure raises DecisionUnavailable so
    the fallback chain (§18) takes over — Laya is an accelerator, never a single point of
    failure."""
    name = "laya"

    def __init__(self, endpoint: str = "http://localhost:8000/decide", *,
                 transport: Transport | None = None, timeout: float = 3.0) -> None:
        self.endpoint = endpoint
        self._transport = transport or _default_transport
        self.timeout = timeout

    def _ask(self, state: ControlState, question: str, choices: list[str] | None) -> Decision:
        t0 = time.monotonic()
        payload = {"state": state.to_prompt(), "question": question,
                   "choices": choices or ["yes", "no"]}
        try:
            raw = self._transport(self.endpoint, payload, self.timeout)
            obj = json.loads(raw)
            options = obj.get("options") if isinstance(obj, dict) else None
            if not isinstance(options, dict) or not options:
                raise DecisionUnavailable("Laya returned no options")
            opts = {str(k): float(v) for k, v in options.items()}
        except DecisionUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — any transport/parse failure -> fall back
            raise DecisionUnavailable(f"Laya unavailable: {e}") from e
        return Decision.from_scores(question, opts, source=self.name,
                                    latency_ms=(time.monotonic() - t0) * 1000)

    def boolean(self, state: ControlState, question: str) -> Decision:
        return self._ask(state, question, ["yes", "no"])

    def choose(self, state: ControlState, question: str, choices: list[str]) -> Decision:
        return self._ask(state, question, choices)

    def score(self, state: ControlState, criterion: str) -> float:
        d = self._ask(state, f"score: {criterion}", ["low", "high"])
        return float(d.options.get("high", 0.0))


# ======================================================================================
# Fallback chain (§18) + confidence router (§16).
# ======================================================================================
class FallbackDecisionProvider:
    """Try providers in order; on DecisionUnavailable OR an uncertain answer (below the
    fallback threshold) move to the next (§16/§18). The last provider's answer is always
    returned, so a decision is always produced — HeuristicProvider should be last."""
    name = "fallback"

    def __init__(self, providers: list[DecisionProvider]) -> None:
        if not providers:
            raise ValueError("FallbackDecisionProvider needs at least one provider")
        self.providers = providers

    def _run(self, call: Callable[[DecisionProvider], Decision]) -> Decision:
        last: Decision | None = None
        for p in self.providers:
            try:
                d = call(p)
            except DecisionUnavailable:
                continue
            last = d
            if not d.uncertain:
                return d
        if last is None:
            # every provider was unavailable: fall to a pure abstention rather than raise.
            return Decision(question="", source="none")
        return last

    def boolean(self, state: ControlState, question: str) -> Decision:
        return self._run(lambda p: p.boolean(state, question))

    def choose(self, state: ControlState, question: str, choices: list[str]) -> Decision:
        return self._run(lambda p: p.choose(state, question, choices))

    def score(self, state: ControlState, criterion: str) -> float:
        for p in self.providers:
            try:
                return p.score(state, criterion)
            except DecisionUnavailable:
                continue
        return 0.5


# ======================================================================================
# Named decisions (§9) — built on the primitives so every provider gets them for free.
# ======================================================================================
_NEXT_ACTIONS = ["continue", "reason_deeper", "retrieve_context", "run_tool",
                 "spawn_agents", "change_strategy", "finish"]
_REASONING_LEVELS = ["minimal", "low", "medium", "high", "maximum"]
_LIMITING_RESOURCES = ["more_reasoning", "more_context", "more_agents", "more_tools",
                       "different_strategy", "none"]

# map a limiting-resource verdict onto an ABC escalation axis (§11 coupling).
RESOURCE_TO_AXIS = {
    "more_reasoning": "reasoning",
    "more_context": "context",
    "more_agents": "agents",
    "more_tools": "tools",
    "different_strategy": "long_horizon",
    "none": "",
}


def decide_continue(provider: DecisionProvider, state: ControlState) -> Decision:
    return provider.boolean(state, "Continue the current approach?")


def decide_next_action(provider: DecisionProvider, state: ControlState) -> Decision:
    choices = state.available_actions or _NEXT_ACTIONS
    return provider.choose(state, "What should happen next?", choices)


def decide_reasoning(provider: DecisionProvider, state: ControlState) -> Decision:
    return provider.choose(state, "What reasoning budget should the next worker receive?",
                           _REASONING_LEVELS)


def decide_limiting_resource(provider: DecisionProvider, state: ControlState) -> Decision:
    """§11 — which resource is most likely limiting progress. Feeds ABC's targeted
    escalation: RESOURCE_TO_AXIS maps the verdict onto an AdaptiveBudgetController axis."""
    return provider.choose(state, "What resource is most limiting progress?",
                           _LIMITING_RESOURCES)


# ======================================================================================
# Decision trace (§20) — state -> decision -> outcome, the seed of a learned scheduler (§21).
# ======================================================================================
class DecisionTrace:
    """Append-only JSONL log of decisions and (later) their outcomes. Never raises: a trace
    that breaks the run defeats its purpose."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.records: list[dict] = []

    def log(self, state: ControlState, decision: Decision, *, outcome: dict | None = None) -> dict:
        rec = {
            "ts": time.time(),
            "state": state.to_dict(),
            "question": decision.question,
            "options": decision.options,
            "selected": decision.selected,
            "confidence": decision.confidence,
            "source": decision.source,
            "latency_ms": round(decision.latency_ms, 2),
            "outcome": outcome or {},
        }
        self.records.append(rec)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            except Exception:  # noqa: BLE001 — logging must never break the run
                pass
        return rec


# ======================================================================================
# Factory (§19) — build the configured provider + fallback chain from config.
# ======================================================================================
def decision_provider(config: dict | None = None, *,
                      laya_transport: Transport | None = None) -> DecisionProvider:
    """Assemble the decision provider from config (§19). Unknown/absent -> heuristic only.
    A Laya or LLM primary is always backed by the heuristic so a decision is always produced
    (§18). `laya_transport` is injectable for tests."""
    config = config or {}
    dp = config.get("decision_plane") or {}
    if not dp.get("enabled", False):
        return HeuristicProvider()

    provider = str(dp.get("provider", "heuristic")).lower()
    chain: list[DecisionProvider] = []
    if provider == "laya":
        chain.append(LayaProvider(
            endpoint=str(dp.get("endpoint", "http://localhost:8000/decide")),
            transport=laya_transport,
        ))
    # (an "llm" primary/fallback would append an LLMDecisionProvider here once built.)
    chain.append(HeuristicProvider())  # always last: the guaranteed answer (§18).
    return FallbackDecisionProvider(chain) if len(chain) > 1 else chain[0]
