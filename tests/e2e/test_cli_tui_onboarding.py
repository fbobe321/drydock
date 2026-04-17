from __future__ import annotations

from pathlib import Path

import pexpect
import pytest

from tests.e2e.common import SpawnedDrydockProcessFixture, ansi_tolerant_pattern


@pytest.mark.timeout(15)
def test_spawn_cli_shows_onboarding_when_api_key_missing(
    tmp_path: Path,
    e2e_workdir: Path,
    spawned_drydock_process: SpawnedDrydockProcessFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drydock_home = tmp_path / "drydock-home-onboarding"
    drydock_home.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("DRYDOCK_HOME", str(drydock_home))
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    with spawned_drydock_process(e2e_workdir) as (child, captured):
        child.expect(ansi_tolerant_pattern("Welcome to Drydock"), timeout=15)
        child.sendcontrol("c")
        child.expect(pexpect.EOF, timeout=10)

    output = captured.getvalue()
    assert "Setup cancelled" in output
