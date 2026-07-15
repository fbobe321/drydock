"""Separated budgets (PRD Epic N) — task vs request vs session.

The agent loop conflated these: it bounded itself on a CUMULATIVE turn counter, so
a long interactive session slowly starved each new request of iterations. The PRD
asks for three distinct scopes:

  * request — model iterations for ONE user message; resets every message (N1.1);
  * task    — tool calls / recovery attempts for the current piece of work;
  * session — cumulative totals across the whole conversation; telemetry only,
              never blocking unless a session policy is explicitly set (N1.2).

When the tool-call budget is exhausted the loop stops running tools and the task
cannot claim unverified success (N1.3). Plain accounting object, never raises.
All logic original to Drydock.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetState:
    # limits (0 / <=0 == unlimited where noted)
    max_model_iterations: int = 200   # per REQUEST
    max_tool_calls: int = 0           # per task (0 = unlimited)
    max_recovery_attempts: int = 0    # per task (0 = unlimited)

    # session-cumulative counters (never reset within a session)
    session_turns: int = 0
    session_tool_calls: int = 0

    # request/task-scoped counters (reset each user message)
    request_iterations: int = 0
    task_tool_calls: int = 0
    recovery_attempts: int = 0

    def start_request(self) -> None:
        """Begin a new user request: reset the per-request and per-task counters,
        leaving the session totals intact (PRD N1.1/N1.2)."""
        self.request_iterations = 0
        self.task_tool_calls = 0
        self.recovery_attempts = 0

    def record_turn(self) -> None:
        self.request_iterations += 1
        self.session_turns += 1

    def iterations_exhausted(self) -> bool:
        return self.request_iterations >= self.max_model_iterations

    def record_tool_call(self) -> None:
        self.task_tool_calls += 1
        self.session_tool_calls += 1

    def tool_budget_exhausted(self) -> bool:
        return self.max_tool_calls > 0 and self.task_tool_calls > self.max_tool_calls

    def record_recovery(self) -> None:
        self.recovery_attempts += 1

    def recovery_exhausted(self) -> bool:
        return self.max_recovery_attempts > 0 and self.recovery_attempts >= self.max_recovery_attempts

    def to_dict(self) -> dict:
        return {
            "request_iterations": self.request_iterations,
            "task_tool_calls": self.task_tool_calls,
            "session_turns": self.session_turns,
            "session_tool_calls": self.session_tool_calls,
        }
