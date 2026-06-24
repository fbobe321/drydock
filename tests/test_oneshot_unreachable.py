"""One-shot (-p) mode must surface an unreachable model server as the friendly,
actionable message + a non-zero exit — not a raw traceback. Regression for the
gap where harbor/automation saw an opaque crash when a backend went down
(the dead-Jetson incident: every trial routed to it died with "stderr: None")."""
from __future__ import annotations

import pytest

from drydock import cli
from drydock.providers import LLMUnreachable


def test_oneshot_unreachable_prints_message_and_exits(monkeypatch, capsys):
    msg = "Cannot reach the LLM at http://127.0.0.1:9/v1 (provider: vllm)."

    def fake_run(prompt, state, config, system):
        raise LLMUnreachable(msg)
        yield  # pragma: no cover — make it a generator

    monkeypatch.setattr(cli, "run", fake_run)

    with pytest.raises(SystemExit) as exc:
        cli.run_oneshot("hello", {"cwd": "."})
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert msg in err
    assert "Traceback" not in err  # the whole point: no raw traceback


def test_oneshot_normal_run_does_not_exit(monkeypatch, capsys):
    from drydock.agent import TextChunk

    def fake_run(prompt, state, config, system):
        yield TextChunk("done")

    monkeypatch.setattr(cli, "run", fake_run)
    cli.run_oneshot("hello", {"cwd": "."})  # must NOT raise SystemExit
    assert "done" in capsys.readouterr().out
