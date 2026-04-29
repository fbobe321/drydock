"""Regression test: circuit breaker count escalates on repeated fires.

Bug: _circuit_breaker_check returned the NOTE but never incremented the count
in _tool_call_history when it fired.  Every retry got the same "8 times" message,
so the escalation signal was flat and the model never broke out.

Fix: _circuit_breaker_check now increments the count on each fire, giving the
model an increasing signal that nothing has changed.
"""
import unittest
from unittest.mock import MagicMock


def _make_loop():
    from drydock.core.agent_loop import AgentLoop
    loop = AgentLoop.__new__(AgentLoop)
    loop._tool_call_history = {}
    loop._get_attempted_summary = lambda: ""
    return loop


def _make_tc(name, args):
    tc = MagicMock()
    tc.tool_name = name
    tc.args_dict = args
    tc.call_id = "test-id"
    return tc


def _record(loop, tc, result="output"):
    from drydock.core.agent_loop import AgentLoop
    AgentLoop._circuit_breaker_record(loop, tc, result)


class TestCircuitBreakerCountEscalation(unittest.TestCase):
    def test_count_escalates_on_repeated_fires(self):
        """After the threshold, each fire increments the count so the message escalates."""
        loop = _make_loop()
        tc = _make_tc("bash", {"command": "python3 -m tool_agent run foo"})

        # Reach threshold (8 recorded calls)
        for _ in range(8):
            _record(loop, tc, "some output")

        # First fire: should mention 8
        msg1 = loop._circuit_breaker_check(tc)
        self.assertIsNotNone(msg1)
        self.assertIn("8 times", msg1)

        # Second fire (without any external record call): should mention 9
        msg2 = loop._circuit_breaker_check(tc)
        self.assertIsNotNone(msg2)
        self.assertIn("9 times", msg2)

        # Third fire: should mention 10
        msg3 = loop._circuit_breaker_check(tc)
        self.assertIsNotNone(msg3)
        self.assertIn("10 times", msg3)

    def test_last_result_preserved_across_fires(self):
        """last_result shown in NOTE stays as the real tool output, not a prior NOTE."""
        loop = _make_loop()
        tc = _make_tc("bash", {"command": "echo hello"})

        for _ in range(8):
            _record(loop, tc, "hello\n")

        msg1 = loop._circuit_breaker_check(tc)
        self.assertIn("hello", msg1)

        # After a second fire, last_result should still be the original output
        msg2 = loop._circuit_breaker_check(tc)
        self.assertIn("hello", msg2)
        # Not the prior NOTE text
        self.assertNotIn("NOTE: this exact call", msg2.split("Last result:")[1])

    def test_count_below_threshold_unaffected(self):
        """Calls below threshold still return None and don't mutate state unexpectedly."""
        loop = _make_loop()
        tc = _make_tc("bash", {"command": "ls"})

        for i in range(7):
            result = loop._circuit_breaker_check(tc)
            self.assertIsNone(result, f"Should not fire on call {i+1} (count={i})")
            _record(loop, tc)

        # 8th call is still below threshold (count=7 < 8)
        result = loop._circuit_breaker_check(tc)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
