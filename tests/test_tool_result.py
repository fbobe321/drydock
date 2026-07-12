"""Structured tool results (Agent-Buildout PRD 7.4): execute_structured() wraps a
tool's string output in a ToolResult (status/error/retryable/changed_state/duration)
while execute() stays a plain string (backward compatibility)."""
from __future__ import annotations

from drydock.tool_result import ToolResult, classify
from drydock.tools import register_all
from drydock import tool_registry

register_all()


def test_execute_still_returns_str():
    r = tool_registry.execute("Bash", {"command": "echo hi", "timeout": 5}, {"cwd": "/tmp"})
    assert isinstance(r, str) and "hi" in r


def test_structured_success():
    r = tool_registry.execute_structured("Bash", {"command": "echo hi", "timeout": 5}, {"cwd": "/tmp"})
    assert isinstance(r, ToolResult) and r.status == "ok" and r.duration_ms >= 0 and not r.changed_state


def test_structured_failure_exit_code():
    r = tool_registry.execute_structured(
        "Bash", {"command": "python3 -c 'import sys;sys.exit(3)'", "timeout": 5}, {"cwd": "/tmp"})
    assert r.status == "error" and r.exit_code == 3


def test_not_found():
    r = tool_registry.execute_structured("NoSuchTool", {}, {})
    assert r.status == "not_found" and r.error_code == "not_found"


def test_classify_changed_state_and_retryable():
    assert classify("Write", {}, "wrote 10 bytes", 1).changed_state
    assert not classify("Read", {}, "contents", 1).changed_state
    assert classify("Bash", {"command": "touch x"}, "", 1).changed_state
    assert not classify("Bash", {"command": "ls -la"}, "files", 1).changed_state
    r = classify("Bash", {"command": "curl x"}, "Error: connection refused", 1)
    assert r.status == "error" and r.retryable and r.error_code == "transient"


def test_error_prefix_string():
    r = classify("Edit", {}, "Error: old_string not found", 1)
    assert r.status == "error" and not r.changed_state   # failed edit didn't mutate


def test_to_event_is_compact():
    ev = classify("Bash", {"command": "echo x"}, "x", 5).to_event()
    assert ev["status"] == "ok" and "result_chars" in ev and "text" not in ev
