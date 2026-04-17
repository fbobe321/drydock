"""Phase 3a/3b unit tests."""
from __future__ import annotations

import json

import pytest

from drydock.admiral import metrics, persistence, policy, task_classifier, tuning
from drydock.admiral.proposer import _fingerprint, _parse
from drydock.core.types import FunctionCall, LLMMessage, Role, ToolCall


# ─── task_classifier ──────────────────────────────────────────────────────


def _user(text: str) -> LLMMessage:
    return LLMMessage(role=Role.user, content=text)


def _asst(tool: str) -> LLMMessage:
    return LLMMessage(
        role=Role.assistant,
        content="",
        tool_calls=[ToolCall(function=FunctionCall(name=tool, arguments="{}"))],
    )


def test_classify_explore_from_keyword() -> None:
    msgs = [_user("explain how the parser works")] + [_asst("read_file")] * 5
    assert task_classifier.classify(msgs) == "explore"


def test_classify_bugfix_from_keyword() -> None:
    msgs = [_user("fix the failing test in test_foo.py")] + [_asst("read_file")] * 3 + [_asst("search_replace")]
    assert task_classifier.classify(msgs) == "bugfix"


def test_classify_build_from_writes() -> None:
    msgs = [_user("hi")] + [_asst("write_file") for _ in range(8)]
    assert task_classifier.classify(msgs) == "build"


def test_classify_unknown_when_too_few() -> None:
    msgs = [_user("?"), _asst("read_file")]
    assert task_classifier.classify(msgs) == "unknown"


# ─── tuning ───────────────────────────────────────────────────────────────


def test_tuning_clips_to_bounds(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(persistence, "TUNING_PATH", tmp_path / "t.json")
    tuning.set_knob("gemma4", "build", "per_prompt_budget_sec", 9999, rationale="too big")
    out = tuning.get_for("gemma4", "build")
    assert out["per_prompt_budget_sec"] == 3600  # clipped to upper bound
    tuning.set_knob("gemma4", "build", "per_prompt_budget_sec", 1, rationale="too small")
    out = tuning.get_for("gemma4", "build")
    assert out["per_prompt_budget_sec"] == 300   # clipped to lower bound


def test_tuning_unknown_knob_raises() -> None:
    with pytest.raises(ValueError):
        tuning.set_knob("gemma4", "build", "nonexistent", 1)


def test_tuning_unknown_task_normalises_to_unknown(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(persistence, "TUNING_PATH", tmp_path / "t.json")
    tuning.set_knob("gemma4", "weirdtask", "temperature", 0.5)
    # both "weirdtask" and "unknown" lookups return the same
    assert tuning.get_for("gemma4", "unknown")["temperature"] == 0.5
    assert tuning.get_for("gemma4", "weirdtask")["temperature"] == 0.5


# ─── policy ───────────────────────────────────────────────────────────────


def test_policy_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("DRYDOCK_ADMIRAL_POLICY", raising=False)
    assert policy.evaluate() == []


def test_policy_does_nothing_when_no_metrics(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DRYDOCK_ADMIRAL_POLICY", "1")
    monkeypatch.setattr(persistence, "METRICS_PATH", tmp_path / "metrics.jsonl")
    assert policy.evaluate() == []


def test_policy_picks_budget_knob_when_budget_hits(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DRYDOCK_ADMIRAL_POLICY", "1")
    metrics_p = tmp_path / "metrics.jsonl"
    tuning_p = tmp_path / "tuning.json"
    monkeypatch.setattr(persistence, "METRICS_PATH", metrics_p)
    monkeypatch.setattr(persistence, "TUNING_PATH", tuning_p)
    # Baseline: 5 successes, no budget hits.
    for _ in range(5):
        metrics_p.parent.mkdir(parents=True, exist_ok=True)
        with metrics_p.open("a") as f:
            f.write(json.dumps({"model": "gemma4", "task_type": "build",
                                "outcome": "success", "per_prompt_budget_hits": 0,
                                "loop_fires": 0, "struggle_fires": 0}) + "\n")
    # Recent: 5 failures with budget hits.
    for _ in range(5):
        with metrics_p.open("a") as f:
            f.write(json.dumps({"model": "gemma4", "task_type": "build",
                                "outcome": "failure", "per_prompt_budget_hits": 1,
                                "loop_fires": 0, "struggle_fires": 0}) + "\n")
    changes = policy.evaluate()
    assert len(changes) == 1
    assert changes[0]["knob"] == "per_prompt_budget_sec"


# ─── proposer parsing ─────────────────────────────────────────────────────


def test_proposer_parse_valid_response() -> None:
    text = (
        "DIRECTIVES VIOLATED: [B5, B8]\n"
        "RATIONALE: agent re-reads the same file 12 times without writing.\n"
        "DIFF:\n"
        "```diff\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "```"
    )
    parsed = _parse(text)
    assert parsed is not None
    dirs, rat, diff = parsed
    assert dirs == ["B5", "B8"]
    assert "re-reads" in rat
    assert "+new" in diff


def test_proposer_parse_returns_none_when_no_diff() -> None:
    assert _parse("RATIONALE: nope.") is None


def test_proposer_fingerprint_is_stable() -> None:
    diff = "--- a\n+++ b\n@@\n+x\n"
    assert _fingerprint(diff) == _fingerprint(diff + "\n")
    assert _fingerprint(diff) != _fingerprint(diff + "y")


# ─── persistence promotion ────────────────────────────────────────────────


def test_finding_qualifies_only_after_three_sessions(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(persistence, "STATE_PATH", tmp_path / "s.json")
    code = "loop:read_file"
    persistence.record_finding(code, "session-1")
    persistence.record_intervention_outcome(code, unstuck=False)
    assert not persistence.finding_qualifies_for_code_change(code)
    persistence.record_finding(code, "session-2")
    persistence.record_finding(code, "session-3")
    assert persistence.finding_qualifies_for_code_change(code)


def test_metrics_collect_handles_partial_state() -> None:
    class FakeAgentLoop:
        messages: list = []

        class config:
            @staticmethod
            def get_active_model():
                raise RuntimeError("boom")

    m = metrics.collect(FakeAgentLoop(), session_id="abc")
    assert m.model == "unknown"
    assert m.task_type == "unknown"
