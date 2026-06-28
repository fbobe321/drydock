"""Dedicated Version Control tools (drydock/gittools.py): structured, truncated
git for the agent. Tests run in isolated temp repos."""
from __future__ import annotations

import subprocess

import pytest

from drydock import gittools
from drydock.tools import (
    tool_gitstatus, tool_gitdiff, tool_gitlog, tool_gitcommit,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "t@t.com"], tmp_path)
    _git(["config", "user.name", "T"], tmp_path)
    (tmp_path / "a.txt").write_text("hello\n")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-qm", "initial"], tmp_path)
    return tmp_path


def test_status_clean_then_dirty(repo):
    cfg = {"cwd": str(repo)}
    assert "clean" in tool_gitstatus({}, cfg).lower()
    (repo / "a.txt").write_text("hello\nworld\n")
    out = tool_gitstatus({}, cfg)
    assert "a.txt" in out and "clean" not in out.lower()


def test_diff_shows_stat_and_body(repo):
    (repo / "a.txt").write_text("hello\nchanged\n")
    out = tool_gitdiff({}, {"cwd": str(repo)})
    assert "a.txt" in out and "changed" in out and "insertion" in out


def test_diff_no_changes(repo):
    assert "No changes" in tool_gitdiff({}, {"cwd": str(repo)})


def test_log_lists_commits(repo):
    out = tool_gitlog({"n": 5}, {"cwd": str(repo)})
    assert "initial" in out


def test_commit_stages_and_commits(repo):
    (repo / "b.txt").write_text("new file\n")
    out = tool_gitcommit({"message": "add b"}, {"cwd": str(repo)})
    assert "Committed" in out
    assert "add b" in tool_gitlog({}, {"cwd": str(repo)})


def test_commit_empty_message_refused(repo):
    (repo / "b.txt").write_text("x")
    assert "non-empty message" in tool_gitcommit({"message": "  "}, {"cwd": str(repo)})


def test_commit_nothing_to_commit(repo):
    assert "Nothing to commit" in tool_gitcommit({"message": "noop"}, {"cwd": str(repo)})


def test_not_a_repo_is_graceful(tmp_path):
    out = tool_gitstatus({}, {"cwd": str(tmp_path)})
    assert "not a git repository" in out.lower()


def test_diff_truncates_large_body(repo):
    # modify a TRACKED file so it appears in the working-tree diff
    (repo / "a.txt").write_text("\n".join(f"line {i}" for i in range(5000)))
    out = gittools.diff(str(repo), max_chars=1000)
    assert "truncated" in out and len(out) < 4000
