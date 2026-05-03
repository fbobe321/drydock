"""Sandbox eval — gating mechanism for promoting a new vector.

The sandbox runs a fixed prompt set against (a) the unsteered model and
(b) the model with a candidate steering config applied, then diffs the
outputs. Drops the structured comparison into a JSON file the operator
can grep / triage.

For v0 the actual completion call is delegated to a callable the caller
supplies — this keeps the sandbox decoupled from any specific backend
(vLLM, Ollama, mocked). Tests inject a fake completion function; real
deployments wire it to the harness's existing chat-completion path.

A "vector earns promotion" when its sandbox eval passes the operator's
acceptance rule. v0 ships with one built-in rule (regression-free: the
steered output must not produce any of a configurable set of bad
patterns). Customers add their own rules at the call site.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from drydock.steering.applier import (
    SteeringApplier,
    SteeringDecision,
    apply_steering,
)
from drydock.steering.config import SteeringConfig
from drydock.steering.registry import SteeringRegistry


# Type alias — a function the caller provides that runs one prompt
# through the model. The sandbox calls it twice per prompt (with and
# without steering) and diffs the outputs.
CompletionFn = Callable[[str, SteeringDecision], str]


@dataclass
class SandboxPromptResult:
    prompt: str
    baseline_output: str
    steered_output: str
    steered_decision: SteeringDecision
    bad_patterns_in_baseline: list[str] = field(default_factory=list)
    bad_patterns_in_steered: list[str] = field(default_factory=list)


@dataclass
class SandboxSummary:
    config: SteeringConfig
    per_prompt: list[SandboxPromptResult] = field(default_factory=list)
    regressions: int = 0      # bad_patterns appeared in steered but not baseline
    fixes: int = 0            # bad_patterns appeared in baseline but not steered
    unchanged_outputs: int = 0
    distinct_outputs: int = 0

    def passed(self) -> bool:
        """Default acceptance rule: no regressions."""
        return self.regressions == 0

    def write_json(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self._serialisable(), indent=2))

    def _serialisable(self) -> dict:
        out = {
            "modes": [m.name for m in self.config.modes],
            "regressions": self.regressions,
            "fixes": self.fixes,
            "unchanged_outputs": self.unchanged_outputs,
            "distinct_outputs": self.distinct_outputs,
            "passed": self.passed(),
            "per_prompt": [],
        }
        for r in self.per_prompt:
            out["per_prompt"].append({
                "prompt": r.prompt,
                "baseline_output": r.baseline_output,
                "steered_output": r.steered_output,
                "applied": [v.manifest.name for v in r.steered_decision.applied_vectors],
                "skipped_reasons": r.steered_decision.skipped_reasons,
                "bad_patterns_in_baseline": r.bad_patterns_in_baseline,
                "bad_patterns_in_steered": r.bad_patterns_in_steered,
            })
        return out


def run_sandbox(
    config: SteeringConfig,
    *,
    prompts: Sequence[str],
    registry: SteeringRegistry,
    applier: SteeringApplier,
    completion_fn: CompletionFn,
    active_model: str,
    bad_patterns: Sequence[str] = (),
) -> SandboxSummary:
    """Run the sandbox eval. `completion_fn(prompt, decision)` is
    expected to return the model's text response.

    The sandbox calls completion_fn twice per prompt:
    1. With a `SteeringDecision` whose `applied_vectors` is empty —
       the baseline.
    2. With the full applied decision under `config`.

    Outputs are diffed on:
    - Presence of any string in `bad_patterns` (regression / fix)
    - Output equality (unchanged / distinct)
    """
    null_decision = SteeringDecision(
        config=SteeringConfig.disabled(),
        applier_kind="null",
    )
    steered_decision = apply_steering(
        config, registry, applier, active_model=active_model
    )

    summary = SandboxSummary(config=config)

    for prompt in prompts:
        baseline = completion_fn(prompt, null_decision)
        steered = completion_fn(prompt, steered_decision)

        bad_in_base = [p for p in bad_patterns if p in baseline]
        bad_in_steered = [p for p in bad_patterns if p in steered]

        result = SandboxPromptResult(
            prompt=prompt,
            baseline_output=baseline,
            steered_output=steered,
            steered_decision=steered_decision,
            bad_patterns_in_baseline=bad_in_base,
            bad_patterns_in_steered=bad_in_steered,
        )
        summary.per_prompt.append(result)

        # Regression: bad pattern present in steered but absent in baseline.
        if any(p not in bad_in_base for p in bad_in_steered):
            summary.regressions += 1
        # Fix: bad pattern absent in steered but present in baseline.
        if any(p not in bad_in_steered for p in bad_in_base):
            summary.fixes += 1

        if baseline == steered:
            summary.unchanged_outputs += 1
        else:
            summary.distinct_outputs += 1

    return summary
