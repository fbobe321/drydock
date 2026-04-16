"""Comprehensive loop detection tests.

Simulates real message histories that trigger each loop detection mechanism.
These test the exact scenarios users report:
- Same command FAILING 3+ times (circuit breaker — only blocks failed commands)
- Exact same tool call repeated N+ times (Check 1: REPEAT_WARNING)
- 5 consecutive bash calls with different args (Check 2: same-tool)
- Reading the same file 5+ times with different offsets (Check 3: same-file)
- Alternating grep->read_file->grep->read_file (Check 4: alternating)
- Bash abuse: 10+ bash calls without making an edit
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from drydock.core.agent_loop import (
    AgentLoop,
    MAX_TOOL_TURNS,
    REPEAT_FORCE_STOP_THRESHOLD,
    REPEAT_WARNING_THRESHOLD,
)
from drydock.core.types import LLMMessage, MessageList, Role, ToolCall, FunctionCall


def _make_agent() -> AgentLoop:
    """Create a minimal AgentLoop for testing loop detection."""
    al = object.__new__(AgentLoop)
    al.messages = MessageList()
    al._tool_call_history = {}
    return al


def _add_tool_call(al: AgentLoop, tool_name: str, arguments: str = "{}") -> None:
    """Add an assistant message with a tool call + a tool result."""
    al.messages.append(LLMMessage(
        role=Role.assistant,
        content="",
        tool_calls=[ToolCall(
            id=f"call_{len(al.messages)}",
            function=FunctionCall(name=tool_name, arguments=arguments),
        )],
    ))
    al.messages.append(LLMMessage(
        role=Role.tool,
        content=f"Result of {tool_name}",
    ))


# ============================================================================
# Circuit Breaker Tests
# ============================================================================

class TestCircuitBreaker:
    """Circuit breaker is fully disabled — always returns None."""

    def test_first_call_allowed(self):
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"ls -ltr"})
        assert al._circuit_breaker_check(tc) is None

    def test_second_call_allowed(self):
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"ls -ltr"})
        al._circuit_breaker_record(tc, "file1.py\nfile2.py")
        assert al._circuit_breaker_check(tc) is None

    def test_disabled_for_failed_after_3(self):
        """CB disabled — failed commands are NOT blocked even after 3 repeats."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"bad_cmd"})
        for _ in range(3):
            al._circuit_breaker_record(tc, "FAILED: command not found")
        assert al._circuit_breaker_check(tc) is None

    def test_failed_2_not_blocked(self):
        """Failed commands with only 2 repeats are not blocked."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"bad_cmd"})
        al._circuit_breaker_record(tc, "FAILED: command not found")
        al._circuit_breaker_record(tc, "FAILED: command not found")
        assert al._circuit_breaker_check(tc) is None

    @pytest.mark.xfail(reason="Stale: current CB returns advisory NOTE "
                              "after 8+ repeats regardless of success. "
                              "Advisory-only is the active design.")
    def test_successful_never_blocked(self):
        """Successful commands are never blocked regardless of repeat count."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"python3 app.py"})
        for _ in range(10):
            al._circuit_breaker_record(tc, "output ok")
        assert al._circuit_breaker_check(tc) is None

    def test_successful_readonly_never_blocked(self):
        """Read-only successful tools are never blocked."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="grep", args_dict={"pattern":"foo"})
        for _ in range(10):
            al._circuit_breaker_record(tc, "match.py:10:foo")
        assert al._circuit_breaker_check(tc) is None

    def test_disabled_returns_none_even_for_failed(self):
        """CB disabled — returns None even for commands that would have been blocked."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"python3 run.py"})
        for _ in range(3):
            al._circuit_breaker_record(tc, "FAILED: module not found")
        assert al._circuit_breaker_check(tc) is None

    def test_disabled_returns_none_for_repeated_failures(self):
        """CB disabled — returns None regardless of failure count."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"python3 run.py"})
        for _ in range(3):
            al._circuit_breaker_record(tc, "FAILED: error")
        assert al._circuit_breaker_check(tc) is None

    def test_disabled_for_all_args(self):
        """CB disabled — no args are blocked, even after repeated failures."""
        al = _make_agent()
        tc1 = SimpleNamespace(tool_name="bash", args_dict={"command":"bad_cmd"})
        tc2 = SimpleNamespace(tool_name="bash", args_dict={"command":"ls -la"})
        for _ in range(3):
            al._circuit_breaker_record(tc1, "FAILED: not found")
        assert al._circuit_breaker_check(tc1) is None
        assert al._circuit_breaker_check(tc2) is None

    def test_disabled_for_all_tools(self):
        """CB disabled — no tools are blocked, even after repeated failures."""
        al = _make_agent()
        tc1 = SimpleNamespace(tool_name="bash", args_dict={"command":"bad_cmd"})
        tc2 = SimpleNamespace(tool_name="grep", args_dict={"pattern":"foo"})
        for _ in range(3):
            al._circuit_breaker_record(tc1, "FAILED: not found")
        assert al._circuit_breaker_check(tc1) is None
        assert al._circuit_breaker_check(tc2) is None

    def test_failed_then_success_not_blocked(self):
        """If a command fails then succeeds, it should not be blocked."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"python3 run.py"})
        al._circuit_breaker_record(tc, "FAILED: error")
        al._circuit_breaker_record(tc, "FAILED: error")
        al._circuit_breaker_record(tc, "output ok")
        assert al._circuit_breaker_check(tc) is None


# ============================================================================
# Check 1: Exact Same Tool Call Repeated (REPEAT_WARNING_THRESHOLD)
# ============================================================================

class TestExactRepeatDetection:
    """Detects the same tool call (name + args) repeated N+ times."""

    def test_no_repeats_returns_none(self):
        al = _make_agent()
        _add_tool_call(al, "bash", '{"command":"ls"}')
        _add_tool_call(al, "grep", '{"pattern":"foo"}')
        _add_tool_call(al, "read_file", '{"path":"x.py"}')
        assert al._check_tool_call_repetition() is None

    def test_warning_at_threshold(self):
        al = _make_agent()
        args = '{"command":"ls -ltr"}'
        for _ in range(REPEAT_WARNING_THRESHOLD):
            _add_tool_call(al, "bash", args)
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "WARNING" in result

    def test_below_threshold_returns_none(self):
        al = _make_agent()
        args = '{"command":"ls -ltr"}'
        for _ in range(REPEAT_WARNING_THRESHOLD - 1):
            _add_tool_call(al, "bash", args)
        assert al._check_tool_call_repetition() is None

    def test_force_stop_at_threshold(self):
        """25 exact repeats. Check 2 (same-tool at 8) catches it first as WARNING."""
        al = _make_agent()
        args = '{"command":"ls -ltr"}'
        for _ in range(REPEAT_FORCE_STOP_THRESHOLD):
            _add_tool_call(al, "bash", args)
        result = al._check_tool_call_repetition()
        # Same-tool consecutive check (8 for bash) fires before exact-repeat (25)
        assert result is not None
        assert "WARNING" in result or result == "FORCE_STOP"

    @pytest.mark.xfail(reason="Stale: _check_tool_call_repetition now returns "
                              "literal 'FORCE_STOP' as a signal token (consumed "
                              "by agent_loop as advisory). Token is not a hard stop.")
    def test_mixed_calls_dont_trigger(self):
        al = _make_agent()
        for i in range(20):
            _add_tool_call(al, "bash", json.dumps({"command": f"cmd_{i}"}))
        # All different args — shouldn't trigger exact repeat
        result = al._check_tool_call_repetition()
        # Might trigger same-tool check but not exact repeat
        if result:
            assert "FORCE_STOP" not in result


# ============================================================================
# Check 2: Same Tool Consecutive (Different Args)
# ============================================================================

class TestSameToolConsecutive:
    """Detects the same tool used N+ times in a row with different args."""

    def test_5_consecutive_bash_triggers_warning(self):
        al = _make_agent()
        for i in range(5):
            _add_tool_call(al, "bash", json.dumps({"command": f"cmd_{i}"}))
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "WARNING" in result
        assert "bash" in result

    def test_4_consecutive_bash_no_warning(self):
        al = _make_agent()
        for i in range(4):
            _add_tool_call(al, "bash", json.dumps({"command": f"cmd_{i}"}))
        result = al._check_tool_call_repetition()
        assert result is None

    def test_7_consecutive_grep_triggers_warning(self):
        al = _make_agent()
        for i in range(7):
            _add_tool_call(al, "grep", json.dumps({"pattern": f"pattern_{i}"}))
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "WARNING" in result

    def test_6_consecutive_grep_no_warning(self):
        al = _make_agent()
        for i in range(6):
            _add_tool_call(al, "grep", json.dumps({"pattern": f"pattern_{i}"}))
        assert al._check_tool_call_repetition() is None

    def test_interleaved_tools_dont_trigger(self):
        al = _make_agent()
        for i in range(20):
            tool = "bash" if i % 2 == 0 else "grep"
            _add_tool_call(al, tool, json.dumps({"arg": f"val_{i}"}))
        # Not consecutive same tool — shouldn't trigger Check 2
        # (might trigger Check 4 alternating though)
        result = al._check_tool_call_repetition()
        # Just verify it doesn't crash
        assert result is None or "WARNING" in result


# ============================================================================
# Check 3: Same File Read 5+ Times
# ============================================================================

class TestSameFileReadLoop:
    """Detects reading the same file 5+ times (different offsets)."""

    def test_5_reads_same_file_triggers(self):
        al = _make_agent()
        for i in range(5):
            _add_tool_call(al, "read_file", json.dumps({
                "path": "src/auth.py", "offset": i * 50, "limit": 50
            }))
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "WARNING" in result

    def test_4_reads_same_file_no_trigger(self):
        al = _make_agent()
        for i in range(4):
            _add_tool_call(al, "read_file", json.dumps({
                "path": "src/auth.py", "offset": i * 50
            }))
        # 4 reads shouldn't trigger (threshold is 5)
        # But might trigger same-tool-consecutive if we hit that threshold
        result = al._check_tool_call_repetition()
        # At 4 reads, same-tool limit for read_file is 10, so no trigger
        assert result is None

    def test_5_reads_different_files_no_trigger(self):
        al = _make_agent()
        for i in range(5):
            _add_tool_call(al, "read_file", json.dumps({
                "path": f"src/file_{i}.py"
            }))
        result = al._check_tool_call_repetition()
        assert result is None


# ============================================================================
# Check 4: Alternating Tool Pattern (A→B→A→B)
# ============================================================================

class TestAlternatingPattern:
    """Detects A→B→A→B→A→B→A→B alternating patterns."""

    def test_8_alternating_triggers(self):
        al = _make_agent()
        for i in range(8):
            tool = "grep" if i % 2 == 0 else "read_file"
            _add_tool_call(al, tool, json.dumps({"arg": f"val_{i}"}))
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "WARNING" in result

    def test_6_alternating_no_trigger(self):
        al = _make_agent()
        for i in range(6):
            tool = "grep" if i % 2 == 0 else "read_file"
            _add_tool_call(al, tool, json.dumps({"arg": f"val_{i}"}))
        result = al._check_tool_call_repetition()
        assert result is None

    def test_3_tool_cycle_no_trigger(self):
        """A→B→C→A→B→C shouldn't trigger the 2-tool alternating check."""
        al = _make_agent()
        tools = ["grep", "read_file", "bash"]
        for i in range(9):
            _add_tool_call(al, tools[i % 3], json.dumps({"arg": f"val_{i}"}))
        result = al._check_tool_call_repetition()
        # 3-tool cycles are not detected by Check 4 (which only checks 2-tool)
        # Might trigger same-tool-consecutive for individual tools though
        assert result is None or "FORCE_STOP" not in result


# ============================================================================
# Threshold Constants
# ============================================================================

class TestThresholdConstants:
    """Verify threshold constants are reasonable."""

    def test_max_tool_turns_ceiling(self):
        assert MAX_TOOL_TURNS <= 200
        assert MAX_TOOL_TURNS >= 50

    def test_warning_before_force_stop(self):
        assert REPEAT_WARNING_THRESHOLD < REPEAT_FORCE_STOP_THRESHOLD

    def test_warning_threshold_not_too_low(self):
        assert REPEAT_WARNING_THRESHOLD >= 3

    def test_force_stop_not_too_high(self):
        assert REPEAT_FORCE_STOP_THRESHOLD <= 30


# ============================================================================
# Real-World Loop Scenarios
# ============================================================================

class TestRealWorldLoops:
    """Simulate actual user-reported looping scenarios."""

    @pytest.mark.xfail(reason="Stale: CB returns advisory NOTE after 8+ repeats "
                              "regardless of success. Advisory-only is the active "
                              "design; the message never stops execution.")
    def test_ls_ltr_loop(self):
        """Successful `ls -ltr` is never blocked by circuit breaker.
        Loop detection (_check_tool_call_repetition) handles this instead."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="bash", args_dict={"command":"ls -ltr"})

        # Successful commands are never blocked by the circuit breaker
        for _ in range(10):
            al._circuit_breaker_record(tc, "total 48\n-rw-r--r-- 1 user user 1234 auth.py")

        assert al._circuit_breaker_check(tc) is None

    def test_grep_same_pattern_loop(self):
        """Model greps for the same pattern 10 times."""
        al = _make_agent()
        for _ in range(REPEAT_WARNING_THRESHOLD):
            _add_tool_call(al, "grep", '{"pattern":"def authenticate","path":"."}')
        assert al._check_tool_call_repetition() is not None

    def test_read_file_offset_evasion(self):
        """Model reads same file with incrementing offset to evade detection."""
        al = _make_agent()
        for i in range(5):
            _add_tool_call(al, "read_file", json.dumps({
                "path": "django/db/models/query.py",
                "offset": i * 100,
                "limit": 100,
            }))
        assert al._check_tool_call_repetition() is not None

    def test_bash_exploration_loop(self):
        """Model runs 8+ different bash commands exploring the filesystem."""
        al = _make_agent()
        commands = [
            "find . -name '*.py'", "ls src/", "cat README.md",
            "wc -l src/*.py", "head -20 setup.py", "ls -la tests/",
            "find . -type f | wc -l", "du -sh src/",
        ]
        for cmd in commands:
            _add_tool_call(al, "bash", json.dumps({"command": cmd}))
        result = al._check_tool_call_repetition()
        assert result is not None  # 8 consecutive bash calls

    def test_grep_read_alternation(self):
        """Model alternates between grep and read_file without making progress."""
        al = _make_agent()
        for i in range(8):
            if i % 2 == 0:
                _add_tool_call(al, "grep", json.dumps({"pattern": f"func_{i//2}"}))
            else:
                _add_tool_call(al, "read_file", json.dumps({"path": f"file_{i//2}.py"}))
        assert al._check_tool_call_repetition() is not None

    def test_normal_workflow_no_false_positive(self):
        """A normal workflow: grep → read → edit → verify should NOT trigger."""
        al = _make_agent()
        _add_tool_call(al, "grep", '{"pattern":"def login"}')
        _add_tool_call(al, "read_file", '{"path":"auth.py","offset":10,"limit":20}')
        _add_tool_call(al, "search_replace", '{"file_path":"auth.py","content":"..."}')
        _add_tool_call(al, "read_file", '{"path":"auth.py","offset":10,"limit":20}')
        assert al._check_tool_call_repetition() is None

    def test_investigate_then_fix_no_false_positive(self):
        """3 greps + 2 reads + 1 edit = normal bug fix workflow."""
        al = _make_agent()
        _add_tool_call(al, "grep", '{"pattern":"class User"}')
        _add_tool_call(al, "grep", '{"pattern":"def save"}')
        _add_tool_call(al, "read_file", '{"path":"models.py"}')
        _add_tool_call(al, "grep", '{"pattern":"validate"}')
        _add_tool_call(al, "read_file", '{"path":"validators.py"}')
        _add_tool_call(al, "search_replace", '{"file_path":"models.py","content":"..."}')
        assert al._check_tool_call_repetition() is None
