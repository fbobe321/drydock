"""Tests for tool-argument validation + deterministic repair (PRD Epic G)."""
from __future__ import annotations

from drydock.tool_validate import (
    repair_and_validate,
    repair_args,
    validate_args,
)

# A representative tool schema.
BASH = {"name": "Bash", "input_schema": {
    "type": "object",
    "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
    "required": ["command"],
}}
READ = {"name": "Read", "input_schema": {
    "type": "object",
    "properties": {"file_path": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["file_path"],
}}


def test_valid_args_pass():
    # PRD G1.1
    assert validate_args(BASH, {"command": "ls"}) == []


def test_missing_required_property_flagged():
    # PRD G1.2
    errs = validate_args(READ, {"limit": 10})
    assert any("file_path" in e for e in errs)


def test_deterministic_trailing_comma_repair():
    # PRD G1.3: only-a-trailing-comma raw JSON re-parses cleanly.
    args = {"_raw": '{"command": "ls",}'}
    repaired, notes = repair_args(BASH, args)
    assert repaired == {"command": "ls"}
    assert any("re-parsed" in n for n in notes)


def test_type_coercion_string_to_int():
    # PRD G1.4: schema wants integer, model sent "10".
    repaired, notes = repair_args(READ, {"file_path": "a.py", "limit": "10"})
    assert repaired["limit"] == 10 and isinstance(repaired["limit"], int)
    assert any("limit" in n for n in notes)


def test_boolean_coercion():
    schema = {"input_schema": {"type": "object",
              "properties": {"replace_all": {"type": "boolean"}}}}
    repaired, _ = repair_args(schema, {"replace_all": "true"})
    assert repaired["replace_all"] is True


def test_no_coercion_for_ambiguous_values():
    # A non-numeric string for an int must NOT be silently coerced; validation flags it.
    repaired, errs, _ = repair_and_validate(READ, {"file_path": "a.py", "limit": "lots"})
    assert repaired["limit"] == "lots"
    assert any("limit" in e for e in errs)


def test_extra_properties_allowed():
    # A slightly-off schema shouldn't block a workable call.
    assert validate_args(BASH, {"command": "ls", "unknown": 1}) == []


def test_bool_not_accepted_as_integer():
    errs = validate_args(READ, {"file_path": "a.py", "limit": True})
    assert any("limit" in e for e in errs)


def test_raw_that_cannot_parse_is_invalid():
    repaired, errs, _ = repair_and_validate(BASH, {"_raw": "not json at all"})
    assert errs  # unparseable -> invalid, not executed


def test_repair_and_validate_happy_path():
    repaired, errs, notes = repair_and_validate(READ, {"file_path": "a.py", "limit": "5"})
    assert errs == []
    assert repaired["limit"] == 5


def test_missing_schema_validates_trivially():
    assert validate_args({"name": "X"}, {"anything": 1}) == []


def test_invalid_call_is_rejected_in_the_loop_not_executed(monkeypatch):
    # End-to-end: a Bash call missing its required 'command' must come back as a
    # typed INVALID_ARGUMENTS message without executing, then the model finishes.
    import tempfile

    import drydock.agent as agent_mod
    from drydock.agent import AgentState, run
    from drydock.providers import AssistantTurn

    turns = [
        AssistantTurn("", [{"id": "1", "name": "Bash", "input": {}}], 1, 1),  # no command
        AssistantTurn("ok, giving up on that", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    list(run("run something", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    tool_msgs = [m for m in st.messages if m.get("role") == "tool"]
    assert tool_msgs and "INVALID_ARGUMENTS" in tool_msgs[0]["content"]


def test_string_int_is_repaired_then_executes(monkeypatch):
    # Read with limit "3" (string) is coerced to int and the call runs.
    import tempfile

    import drydock.agent as agent_mod
    from drydock.agent import AgentState, run
    from drydock.providers import AssistantTurn

    d = tempfile.mkdtemp()
    with open(f"{d}/f.txt", "w") as fh:
        fh.write("a\nb\nc\nd\n")
    turns = [
        AssistantTurn("", [{"id": "1", "name": "Read",
                            "input": {"file_path": f"{d}/f.txt", "limit": "3"}}], 1, 1),
        AssistantTurn("read it", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    list(run("read the file", st,
             {"model": "m", "cwd": d, "verify_gate": False}, "sys"))
    tool_msgs = [m for m in st.messages if m.get("role") == "tool"]
    assert tool_msgs and "INVALID_ARGUMENTS" not in tool_msgs[0]["content"]
