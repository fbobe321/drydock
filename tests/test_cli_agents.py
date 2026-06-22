"""Drydock must LOAD a user's own AGENTS.md/DRYDOCK.md as background context,
but must NEVER auto-create one (it littered every working directory and its
'ACT IMMEDIATELY, implement the PRD' content fought the system prompt)."""
from __future__ import annotations

import os

from drydock import cli


def _in_dir(tmp_path, fn):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return fn()
    finally:
        os.chdir(cwd)


def test_no_agents_md_is_created(tmp_path):
    # An empty project must stay empty after loading instructions.
    text = _in_dir(tmp_path, cli.load_project_instructions)
    assert text == ""
    assert list(tmp_path.iterdir()) == []  # nothing written
    assert not hasattr(cli, "ensure_agents_md")  # the auto-creator is gone


def test_existing_agents_md_loaded_as_background(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use 4-space indent. Prefer pathlib.")
    text = _in_dir(tmp_path, cli.load_project_instructions)
    assert "Use 4-space indent" in text
    # Framed as background context, NOT a command to act now.
    assert "background context" in text.lower()
    assert "not a" in text.lower() and "act now" in text.lower()
