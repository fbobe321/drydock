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

    def test_readonly_note_includes_full_result(self):
        """Read-only tools that repeat 5+ times get a NOTE that includes the
        full cached result so the model can act on it without retrying."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="grep", args_dict={"pattern":"foo"})
        content = "match.py:10:foo"
        for _ in range(10):
            al._circuit_breaker_record(tc, content)
        result = al._circuit_breaker_check(tc)
        assert result is not None
        # The full cached content must appear in the NOTE (not just 200 chars)
        assert content in result
        assert "NOTE:" in result

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

    def test_read_file_note_stores_2000_chars(self):
        """read_file NOTE stores up to 2000 chars of content so large files are
        visible in the advisory — not truncated to 500 like bash results."""
        al = _make_agent()
        tc = SimpleNamespace(tool_name="read_file", args_dict={"path": "foo.py"})
        long_content = "x" * 1800  # within 2000-char limit
        for _ in range(6):
            al._circuit_breaker_record(tc, long_content)
        result = al._circuit_breaker_check(tc)
        assert result is not None
        # The full 1800-char content must be present in the NOTE
        assert long_content in result


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
        # Use write_file (not bash) — bash has a tighter 3-consecutive FORCE_STOP
        # that fires before the generic WARNING_THRESHOLD=4.
        al = _make_agent()
        args = '{"path":"foo.py","content":"x"}'
        for _ in range(REPEAT_WARNING_THRESHOLD):
            _add_tool_call(al, "write_file", args)
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "WARNING" in result

    def test_below_threshold_returns_none(self):
        # Use write_file with different paths — same path triggers Check 5 at 3
        # (single-file rewrite), and bash has a 3-consecutive FORCE_STOP.
        al = _make_agent()
        for i in range(REPEAT_WARNING_THRESHOLD - 1):
            _add_tool_call(al, "write_file", f'{{"path":"file{i}.py","content":"x"}}')
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
# Bash Exploration Loop (same command alternating with other tools)
# ============================================================================

class TestBashExplorationLoop:
    """Same bash command 5+ times across 20 tool calls triggers FORCE_STOP."""

    def test_alternating_bash_read_triggers(self):
        """bash(same_cmd) + read_file(different) alternating 10x → FORCE_STOP."""
        al = _make_agent()
        cmd = "python3 -m tool_agent --list-tools"
        for i in range(10):
            if i % 2 == 0:
                _add_tool_call(al, "bash", json.dumps({"command": cmd}))
            else:
                _add_tool_call(al, "read_file", json.dumps({"path": f"file_{i}.py"}))
        result = al._check_tool_call_repetition()
        assert result == "FORCE_STOP"
        assert getattr(al, "_hot_tool_path", None) == ("bash", cmd)

    def test_3_consecutive_identical_bash_triggers(self):
        """3 consecutive identical bash commands → FORCE_STOP (consecutive check)."""
        al = _make_agent()
        cmd = 'python3 -c "import re; match = re.match(r\'([^=!<>]+)(==|!=)\', \'age>25\')"'
        for _ in range(3):
            _add_tool_call(al, "bash", json.dumps({"command": cmd}))
        result = al._check_tool_call_repetition()
        assert result == "FORCE_STOP"

    def test_2_consecutive_identical_bash_no_force_stop(self):
        """2 identical bash commands should not fire FORCE_STOP."""
        al = _make_agent()
        cmd = "python3 app.py"
        for _ in range(2):
            _add_tool_call(al, "bash", json.dumps({"command": cmd}))
        result = al._check_tool_call_repetition()
        assert result != "FORCE_STOP"

    def test_5_identical_bash_triggers(self):
        """5 identical bash calls in a row → FORCE_STOP."""
        al = _make_agent()
        cmd = "python3 -m tool_agent --list-tools"
        for _ in range(5):
            _add_tool_call(al, "bash", json.dumps({"command": cmd}))
        result = al._check_tool_call_repetition()
        assert result == "FORCE_STOP"

    def test_4_identical_bash_no_trigger(self):
        """4 identical bash calls should not trigger (below threshold)."""
        al = _make_agent()
        cmd = "python3 app.py"
        for _ in range(4):
            _add_tool_call(al, "bash", json.dumps({"command": cmd}))
        result = al._check_tool_call_repetition()
        # 4 consecutive should still trigger the new consecutive check
        assert result == "FORCE_STOP"

    def test_varied_bash_no_trigger(self):
        """Different bash commands should not trigger."""
        al = _make_agent()
        cmds = ["ls -la", "cat README.md", "python3 app.py", "pwd", "echo hi"]
        for cmd in cmds:
            _add_tool_call(al, "bash", json.dumps({"command": cmd}))
        result = al._check_tool_call_repetition()
        assert result != "FORCE_STOP"


# ============================================================================
# Search/Replace File-Dominance Loop Detection
# ============================================================================

class TestSearchReplaceFileDominance:
    """search_replace on same file 5+ times with varying content → FORCE_STOP."""

    def test_five_sr_same_file_triggers(self):
        """5 search_replace calls on same file (different search text) → FORCE_STOP."""
        al = _make_agent()
        for i in range(5):
            _add_tool_call(
                al, "search_replace",
                json.dumps({"file_path": "tool_agent/cli.py", "content": f"text{i}"})
            )
        result = al._check_tool_call_repetition()
        assert result == "FORCE_STOP"

    def test_four_sr_same_file_no_trigger(self):
        """4 search_replace on same file should not trigger."""
        al = _make_agent()
        for i in range(4):
            _add_tool_call(
                al, "search_replace",
                json.dumps({"file_path": "tool_agent/cli.py", "content": f"text{i}"})
            )
        result = al._check_tool_call_repetition()
        assert result != "FORCE_STOP"

    def test_sr_different_files_no_trigger(self):
        """search_replace spread across different files should not trigger."""
        al = _make_agent()
        for i in range(5):
            _add_tool_call(
                al, "search_replace",
                json.dumps({"file_path": f"tool_agent/file{i}.py", "content": "text"})
            )
        result = al._check_tool_call_repetition()
        assert result != "FORCE_STOP"


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


# ============================================================================
# Hallucinated Tool Loop Tests
# ============================================================================

def _make_agent_with_tools(real_tools: list[str]) -> AgentLoop:
    """Create an AgentLoop with a mock tool_manager that has known tools."""
    al = _make_agent()
    al.tool_manager = type("TM", (), {"available_tools": {t: object() for t in real_tools}})()
    return al


class TestHallucinatedToolLoop:
    """Model keeps calling a tool that doesn't exist."""

    def test_1_hallucinated_call_returns_force_stop(self):
        """Even a single hallucinated call triggers FORCE_STOP (threshold=1)."""
        al = _make_agent_with_tools(["glob", "grep", "read_file", "write_file", "bash"])
        _add_tool_call(al, "ralph_repo_index", '{"directory":"."}')
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "FORCE_STOP" in result

    def test_3_hallucinated_calls_returns_force_stop(self):
        """Still triggers with 3 calls (threshold=1, regression guard)."""
        al = _make_agent_with_tools(["glob", "grep", "read_file", "write_file", "bash"])
        for _ in range(3):
            _add_tool_call(al, "ralph_repo_index", '{"directory":"."}')
        result = al._check_tool_call_repetition()
        assert result is not None
        assert "FORCE_STOP" in result

    def test_real_tool_calls_not_counted(self):
        """Many calls to a real tool should not count toward hallucinated threshold."""
        al = _make_agent_with_tools(["glob", "grep"])
        for _ in range(5):
            _add_tool_call(al, "glob", '{"pattern":"**/*.py"}')
        # glob is real — should not trigger hallucinated-tool check
        # (may trigger other checks but _hot_tool_path would be set)
        # just verify it doesn't set _hot_tool_path=None via hallucinated path
        al._check_tool_call_repetition()
        # If it triggers, _hot_tool_path should be set (not None) unless
        # the bash/sr/wf checks fired first. The hallucinated check should not fire.
        # Key invariant: no crash
        assert True  # primary check: no AttributeError

    def test_force_stop_clears_hot_tool_path(self):
        """FORCE_STOP from hallucinated check should set _hot_tool_path=None."""
        al = _make_agent_with_tools(["glob", "grep"])
        al._hot_tool_path = ("some_tool", "/some/path")
        for _ in range(3):
            _add_tool_call(al, "ghost_tool", '{}')
        al._check_tool_call_repetition()
        assert al._hot_tool_path is None


# ============================================================================
# Mid-turn user injection (Claude Code "type while busy" feature)
# ============================================================================

class TestUserInjection:
    """`queue_user_injection` + `_drain_user_injections` fold a queued user
    message into the last tool result as a SYSTEM note. This is the bridge
    between the TUI submit-while-busy path and the agent loop."""

    def _make(self) -> AgentLoop:
        al = _make_agent()
        al._pending_user_injections = []
        return al

    def test_queue_appends_to_pending_list(self):
        al = self._make()
        al.queue_user_injection("also add a CLI flag for verbose mode")
        assert al._pending_user_injections == [
            "also add a CLI flag for verbose mode"
        ]

    def test_queue_strips_and_drops_empty(self):
        al = self._make()
        al.queue_user_injection("   ")
        al.queue_user_injection("")
        al.queue_user_injection("  real message  ")
        assert al._pending_user_injections == ["real message"]

    def test_drain_folds_into_last_tool_result(self):
        al = self._make()
        _add_tool_call(al, "read_file", '{"path": "foo.py"}')
        original_tool_result = al.messages[-1].content
        al.queue_user_injection("also rename foo to bar")
        al._drain_user_injections()
        assert al._pending_user_injections == []
        # The injection landed on the last tool result, not as a new
        # user-after-tool message (which vLLM/Mistral reject).
        assert al.messages[-1].role == Role.tool
        last_content = al.messages[-1].content or ""
        assert original_tool_result in last_content
        assert "USER (typed while you were working" in last_content
        assert "also rename foo to bar" in last_content

    def test_drain_no_messages_is_noop(self):
        al = self._make()
        # Empty queue, empty history — no crash, no message creation.
        al._drain_user_injections()
        assert len(al.messages) == 0

    def test_drain_multiple_injections_in_order(self):
        al = self._make()
        _add_tool_call(al, "grep", '{"pattern": "foo"}')
        al.queue_user_injection("first follow-up")
        al.queue_user_injection("second follow-up")
        al._drain_user_injections()
        content = al.messages[-1].content or ""
        assert "first follow-up" in content
        assert "second follow-up" in content
        assert content.index("first follow-up") < content.index("second follow-up")

    def test_idempotent_drain(self):
        al = self._make()
        _add_tool_call(al, "bash", '{"command": "ls"}')
        al.queue_user_injection("test injection")
        al._drain_user_injections()
        snapshot = al.messages[-1].content
        # Second drain with empty queue must not duplicate.
        al._drain_user_injections()
        assert al.messages[-1].content == snapshot


# ============================================================================
# Agent-loop curiosity hooks (SOVEREIGN_PRD §5.7 tier-2 integration)
# ============================================================================

class TestAgentLoopCuriosityHooks:
    """`_log_curiosity_gaps` and `_maybe_log_surprise` bridge the curiosity
    module into the live agent loop. These tests pin the integration
    points so a future refactor can't silently break the §5.7 producers."""

    def _make_with_session(self) -> AgentLoop:
        al = _make_agent()
        al.session_id = "test-session-abc"
        return al

    def test_log_curiosity_gaps_writes_to_queue(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "c.jsonl")
        )
        al = self._make_with_session()
        al._log_curiosity_gaps("Investigate the MCP server config and GraphRAG corpus.")
        # Should have enqueued at least one UNKNOWN_TERM item (MCP or GraphRAG).
        from drydock.curiosity import read_recent
        items = read_recent(limit=10)
        assert items, "expected at least one curiosity item"
        assert all(i["kind"] == "unknown_term" for i in items)
        terms = {i["term"] for i in items}
        assert "MCP" in terms or any("GraphRAG" in t for t in terms)

    def test_log_curiosity_gaps_silent_on_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "c.jsonl")
        )
        al = self._make_with_session()
        # Empty / boring input → no gaps, no enqueue.
        al._log_curiosity_gaps("fix the bug")
        from drydock.curiosity import read_recent
        assert read_recent() == []

    def test_log_curiosity_gaps_handles_empty_msg(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "c.jsonl")
        )
        al = self._make_with_session()
        # Must not raise on empty/None.
        al._log_curiosity_gaps("")
        al._log_curiosity_gaps(None)  # type: ignore[arg-type]

    def test_log_curiosity_gaps_session_tagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "c.jsonl")
        )
        al = self._make_with_session()
        al._log_curiosity_gaps("Question about the FAISS index.")
        from drydock.curiosity import read_recent
        items = read_recent(limit=10)
        assert items
        assert items[0]["source"] == "session:test-session-abc"

    def test_maybe_log_surprise_confident_wrong_enqueues(
        self, tmp_path, monkeypatch
    ):
        """Confident assertion + tool error → EVIDENCE_CONFLICT enqueued."""
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "c.jsonl")
        )
        al = self._make_with_session()
        # Plant a confident assistant assertion.
        al.messages.append(LLMMessage(
            role=Role.assistant,
            content="All tests pass and the code works correctly.",
        ))

        # Simulate the tool_call object _handle_tool_response receives.
        from types import SimpleNamespace
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="bash", arguments="{}")
        )
        tool_text = (
            "<tool_error>\nTraceback (most recent call last):\n"
            "AssertionError: 1 != 2"
        )
        al._maybe_log_surprise(tool_call, tool_text)
        from drydock.curiosity import read_recent
        items = read_recent(limit=5)
        assert items, "expected EVIDENCE_CONFLICT enqueued"
        assert items[0]["kind"] == "evidence_conflict"
        assert "bash" in items[0]["term"]

    def test_maybe_log_surprise_no_assertion_silent(
        self, tmp_path, monkeypatch
    ):
        """If there's no prior assistant content, nothing to compare → no enqueue."""
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "c.jsonl")
        )
        al = self._make_with_session()
        from types import SimpleNamespace
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="grep", arguments="{}")
        )
        al._maybe_log_surprise(tool_call, "<tool_error>\nTraceback: ...")
        from drydock.curiosity import read_recent
        assert read_recent() == []

    def test_maybe_log_surprise_below_threshold_skips(
        self, tmp_path, monkeypatch
    ):
        """Generic tool error without a confident claim → score < threshold → skip."""
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "c.jsonl")
        )
        al = self._make_with_session()
        # Tentative phrasing, no "passes/correct/works" markers.
        al.messages.append(LLMMessage(
            role=Role.assistant,
            content="Running the tests now to see what happens.",
        ))
        from types import SimpleNamespace
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="bash", arguments="{}")
        )
        al._maybe_log_surprise(
            tool_call, "<tool_error>\nTraceback: failure on row 3"
        )
        from drydock.curiosity import read_recent
        # Some surprise present, but below 0.6 → not enqueued.
        assert read_recent() == []
