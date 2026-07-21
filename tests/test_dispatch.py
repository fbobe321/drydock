"""Multi-agent: the Dispatch tool fans out several read-only sub-agents in
parallel and returns all summaries. The sub-agent runner is mocked so tests are
deterministic and don't need the LLM."""
from __future__ import annotations

import time

import drydock.tools as T


def test_dispatch_runs_each_task_and_labels(monkeypatch):
    monkeypatch.setattr(T, "_run_subagent", lambda p, c: f"R:{p}")
    out = T.tool_dispatch({"tasks": [
        {"prompt": "find auth", "label": "auth"},
        {"prompt": "find db"},
    ]}, {})
    assert "=== auth ===" in out and "R:find auth" in out
    assert "=== agent 2 ===" in out and "R:find db" in out


def test_dispatch_accepts_string_tasks(monkeypatch):
    monkeypatch.setattr(T, "_run_subagent", lambda p, c: f"R:{p}")
    out = T.tool_dispatch({"tasks": ["a", "b", "c"]}, {})
    assert out.count("R:") == 3


def test_dispatch_needs_tasks():
    assert "needs a `tasks`" in T.tool_dispatch({"tasks": []}, {})
    assert "needs a `tasks`" in T.tool_dispatch({}, {})


def test_dispatch_caps_fanout(monkeypatch):
    monkeypatch.setattr(T, "_run_subagent", lambda p, c: "x")
    out = T.tool_dispatch({"tasks": [f"t{i}" for i in range(20)]}, {})
    assert "Dispatched 6 sub-agent" in out  # capped at 6


def test_dispatch_isolates_subagent_errors(monkeypatch):
    def runner(p, c):
        if "boom" in p:
            raise RuntimeError("kaboom")
        return f"R:{p}"
    monkeypatch.setattr(T, "_run_subagent", runner)
    out = T.tool_dispatch({"tasks": ["ok", "boom"]}, {})
    assert "R:ok" in out and "agent error" in out  # one fails, the other survives


def test_dispatch_runs_concurrently(monkeypatch):
    # 4 tasks that each sleep 0.2s should finish in well under 0.8s (serial time)
    def slow(p, c):
        time.sleep(0.2)
        return "done"
    monkeypatch.setattr(T, "_run_subagent", slow)
    t0 = time.monotonic()
    T.tool_dispatch({"tasks": ["a", "b", "c", "d"]}, {})
    assert time.monotonic() - t0 < 0.6  # parallel, not 0.8s serial


def test_task_still_works_via_shared_runner(monkeypatch):
    monkeypatch.setattr(T, "_run_subagent", lambda p, c: f"summary:{p}")
    assert T.tool_task({"prompt": "explore X"}, {}) == "summary:explore X"
    assert "needs a `prompt`" in T.tool_task({}, {})


def test_subagent_summary_is_capped():
    """A sub-agent's return is size-capped so its investigation can never bloat
    the main agent's context — only a bounded partition crosses back."""
    from drydock.tools import _cap_summary, _SUBAGENT_SUMMARY_CAP
    assert _cap_summary("brief finding") == "brief finding"        # short passes through
    big = "\n".join(f"line {i} with some detail" for i in range(2000))
    out = _cap_summary(big)
    assert len(out) < _SUBAGENT_SUMMARY_CAP + 200                  # bounded
    assert "truncated" in out and "main context" in out           # explains the cut


def test_dispatch_bare_string_is_one_task(monkeypatch):
    # A bare string (not a list) must be ONE task, not iterated char-by-char.
    monkeypatch.setattr(T, "_run_subagent", lambda p, c: f"R:{p}")
    out = T.tool_dispatch({"tasks": "investigate the parser bug"}, {})
    assert out.count("R:") == 1 and "R:investigate the parser bug" in out


def test_dispatch_non_iterable_tasks_no_crash():
    # tasks as a number must not TypeError on 'for t in raw'; returns the error.
    assert "needs a `tasks`" in T.tool_dispatch({"tasks": 5}, {})


def test_task_prompt_wrong_type_coerced(monkeypatch):
    monkeypatch.setattr(T, "_run_subagent", lambda p, c: f"R:{p}")
    # list prompt joins (not dropped to first element); int coerces; both run.
    assert "R:investigate\nthe bug" in T.tool_task({"prompt": ["investigate", "the bug"]}, {})
    assert "R:42" in T.tool_task({"prompt": 42}, {})
    assert "needs a `prompt`" in T.tool_task({}, {})


def test_worker_prompt_wrong_type_coerced(monkeypatch):
    monkeypatch.setattr(T, "_run_subagent", lambda p, c, **kw: f"R:{p}")
    assert "R:do\nthe thing" in T.tool_worker({"prompt": ["do", "the thing"]}, {})
    assert "needs a `prompt`" in T.tool_worker({"prompt": []}, {})
