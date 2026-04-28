"""Regression test: read_file circuit breaker fires at threshold 5, not 12.

Stress sessions reset every 45 prompts. With threshold=12, the model could
loop 11× on the same read_file call before being blocked — admiral advisories
fire at 3× but are ignored. Threshold=5 actually engages the block before
the session's budget is exhausted.
"""
import hashlib
import json
import unittest
from unittest.mock import MagicMock


def _make_circuit_breaker_check():
    """Import and bind just the circuit breaker logic for unit testing."""
    from drydock.core.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop._tool_call_history = {}
    loop._get_attempted_summary = lambda: ""
    return loop


class TestCircuitBreakerReadonlyThreshold(unittest.TestCase):
    def _make_tool_call(self, name, args):
        tc = MagicMock()
        tc.tool_name = name
        tc.args_dict = args
        tc.call_id = "test-id"
        return tc

    def _record(self, loop, tool_call, result="some content"):
        from drydock.core.agent_loop import AgentLoop
        AgentLoop._circuit_breaker_record(loop, tool_call, result)

    def test_read_file_blocks_at_5(self):
        """Circuit breaker fires on the 6th identical call (threshold=5 means count>=5 blocks)."""
        loop = _make_circuit_breaker_check()
        tc = self._make_tool_call("read_file", {"path": "foo.py", "offset": 150, "limit": 100})

        # First 5 calls: should not block
        for i in range(5):
            result = loop._circuit_breaker_check(tc)
            self.assertIsNone(result, f"Expected no block on call {i+1}, got: {result}")
            self._record(loop, tc)

        # 6th call: should block (count=5 >= threshold=5)
        result = loop._circuit_breaker_check(tc)
        self.assertIsNotNone(result, "Expected circuit breaker to fire on 6th identical read_file")
        self.assertIn("read_file", result)

    def test_bash_still_blocks_at_8(self):
        """bash threshold=8, so blocks on 9th call (count=8 >= threshold=8)."""
        loop = _make_circuit_breaker_check()
        tc = self._make_tool_call("bash", {"command": "ls -la"})

        for i in range(8):
            result = loop._circuit_breaker_check(tc)
            self.assertIsNone(result, f"Expected no block on bash call {i+1}")
            self._record(loop, tc)

        result = loop._circuit_breaker_check(tc)
        self.assertIsNotNone(result, "Expected circuit breaker to fire on 9th identical bash")

    def test_grep_blocks_at_5(self):
        loop = _make_circuit_breaker_check()
        tc = self._make_tool_call("grep", {"pattern": "def foo", "path": "bar.py"})

        for i in range(5):
            result = loop._circuit_breaker_check(tc)
            self.assertIsNone(result)
            self._record(loop, tc)

        # 6th call blocks (count=5 >= threshold=5)
        result = loop._circuit_breaker_check(tc)
        self.assertIsNotNone(result, "Expected circuit breaker to fire on 6th identical grep")

    def test_different_args_not_blocked(self):
        loop = _make_circuit_breaker_check()

        # 5 reads of foo.py at offset 150
        tc1 = self._make_tool_call("read_file", {"path": "foo.py", "offset": 150})
        for _ in range(5):
            self._record(loop, tc1)

        # Different offset should not be blocked
        tc2 = self._make_tool_call("read_file", {"path": "foo.py", "offset": 200})
        result = loop._circuit_breaker_check(tc2)
        self.assertIsNone(result, "Different args should not be blocked")


if __name__ == "__main__":
    unittest.main()
