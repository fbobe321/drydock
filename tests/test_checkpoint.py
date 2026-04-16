"""Checkpoint engine tests — uses real git in tmp dirs (no mocks)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from drydock.core.checkpoint import (
    Checkpoint,
    CheckpointError,
    CheckpointStore,
    _parse_shortstat,
)


def _git_present() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=2)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _git_present(), reason="git not on PATH")


@pytest.fixture()
def store(tmp_path):
    """A CheckpointStore over an empty work-tree, isolated from $HOME."""
    work = tmp_path / "work"
    work.mkdir()
    base = tmp_path / "checkpoints"
    s = CheckpointStore(work_tree=work, session_id="testsess", base_dir=base)
    yield s


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def test_init_creates_bare_repo(store):
    assert (store.git_dir / "HEAD").is_file()
    assert (store.git_dir / "info" / "exclude").is_file()


def test_init_idempotent(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    base = tmp_path / "ck"
    s1 = CheckpointStore(work, "sess", base_dir=base)
    s1.record(msg_index=1, label="first")
    # Re-construct — should pick up existing state.
    s2 = CheckpointStore(work, "sess", base_dir=base)
    assert len(s2.checkpoints) == 1
    assert s2.checkpoints[0].label == "first"


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------

def test_record_empty_worktree(store):
    cp = store.record(msg_index=2, label="empty")
    assert cp.index == 0
    assert cp.commit  # got a real SHA
    assert len(store.checkpoints) == 1


def test_record_with_files(store):
    (store.work_tree / "a.py").write_text("print('hi')\n")
    (store.work_tree / "data.json").write_text('{"x": 1}\n')
    cp = store.record(msg_index=4, label="two files")
    assert cp.commit
    # Verify the files are in the commit's tree
    out = store._git("ls-tree", "-r", "--name-only", cp.commit)
    names = set(out.strip().splitlines())
    assert "a.py" in names
    assert "data.json" in names


def test_record_skips_excluded_dirs(store):
    (store.work_tree / "a.py").write_text("ok\n")
    cache = store.work_tree / "__pycache__"
    cache.mkdir()
    (cache / "junk.pyc").write_text("garbage")
    venv = store.work_tree / ".venv"
    venv.mkdir()
    (venv / "lib.py").write_text("not us")
    cp = store.record(msg_index=1, label="excludes")
    out = store._git("ls-tree", "-r", "--name-only", cp.commit)
    names = set(out.strip().splitlines())
    assert "a.py" in names
    assert not any(n.startswith("__pycache__/") for n in names)
    assert not any(n.startswith(".venv/") for n in names)


def test_record_dedupes_unchanged_tree(store):
    (store.work_tree / "a.py").write_text("ok\n")
    cp1 = store.record(msg_index=1, label="first")
    cp2 = store.record(msg_index=2, label="no change")
    # Same tree → same Checkpoint object (no new commit)
    assert cp1 is cp2
    assert len(store.checkpoints) == 1


def test_record_chains_parents(store):
    (store.work_tree / "a.py").write_text("v1\n")
    cp1 = store.record(msg_index=1, label="v1")
    (store.work_tree / "a.py").write_text("v2\n")
    cp2 = store.record(msg_index=2, label="v2")
    parent = store._git("rev-parse", f"{cp2.commit}^").strip()
    assert parent == cp1.commit


def test_record_stores_agent_state(store):
    (store.work_tree / "a.py").write_text("ok\n")
    cp = store.record(
        msg_index=1, label="x",
        agent_state={"circuit_fires": 3, "loop_detected": True},
    )
    assert cp.agent_state == {"circuit_fires": 3, "loop_detected": True}


def test_record_agent_state_persists_across_load(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    base = tmp_path / "ck"
    s1 = CheckpointStore(work, "sess", base_dir=base)
    (work / "a.py").write_text("a\n")
    s1.record(msg_index=1, label="x",
              agent_state={"errors": 2, "hot_path": ["bash", "ls"]})
    s2 = CheckpointStore(work, "sess", base_dir=base)
    assert s2.checkpoints[0].agent_state == {
        "errors": 2, "hot_path": ["bash", "ls"]
    }


def test_record_agent_state_distinguishes_dedup(store):
    """Same files but different agent_state must NOT dedupe — the caller
    will want the new counters reflected when they later restore."""
    (store.work_tree / "a.py").write_text("ok\n")
    cp1 = store.record(msg_index=1, label="x",
                       agent_state={"fires": 0})
    cp2 = store.record(msg_index=2, label="x",
                       agent_state={"fires": 1})
    assert cp1.commit != cp2.commit or cp1.agent_state != cp2.agent_state
    assert len(store.checkpoints) == 2


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def test_restore_code_reverts_file_contents(store):
    f = store.work_tree / "a.py"
    f.write_text("v1\n")
    cp1 = store.record(msg_index=1, label="v1")
    f.write_text("v2 broken\n")
    store.record(msg_index=2, label="v2")
    store.restore(cp1.index, mode="code")
    assert f.read_text() == "v1\n"


def test_restore_code_removes_files_added_after(store):
    (store.work_tree / "a.py").write_text("a\n")
    cp1 = store.record(msg_index=1, label="just a")
    (store.work_tree / "b.py").write_text("b\n")
    store.record(msg_index=2, label="added b")
    store.restore(cp1.index, mode="code")
    assert (store.work_tree / "a.py").is_file()
    assert not (store.work_tree / "b.py").is_file()


def test_restore_code_brings_back_deleted_files(store):
    f = store.work_tree / "keepme.py"
    f.write_text("keep\n")
    cp1 = store.record(msg_index=1, label="present")
    f.unlink()
    store.record(msg_index=2, label="deleted")
    store.restore(cp1.index, mode="code")
    assert f.is_file() and f.read_text() == "keep\n"


def test_restore_truncates_future_checkpoints(store):
    (store.work_tree / "a.py").write_text("v1\n")
    cp1 = store.record(msg_index=1, label="v1")
    (store.work_tree / "a.py").write_text("v2\n")
    store.record(msg_index=2, label="v2")
    (store.work_tree / "a.py").write_text("v3\n")
    store.record(msg_index=3, label="v3")
    assert len(store.checkpoints) == 3
    store.restore(cp1.index, mode="code")
    # After restoring to cp1, cp2 and cp3 are dropped — future record()
    # extends from cp1.
    assert len(store.checkpoints) == 1
    assert store.checkpoints[-1].commit == cp1.commit


def test_restore_conversation_only_does_not_touch_files(store):
    f = store.work_tree / "a.py"
    f.write_text("v1\n")
    cp1 = store.record(msg_index=1, label="v1")
    f.write_text("v2-still-here\n")
    store.record(msg_index=2, label="v2")
    store.restore(cp1.index, mode="conversation")
    # File untouched
    assert f.read_text() == "v2-still-here\n"


def test_restore_invalid_index_raises(store):
    with pytest.raises(CheckpointError):
        store.restore(99, mode="code")


def test_restore_invalid_mode_raises(store):
    store.record(msg_index=1, label="x")
    with pytest.raises(ValueError):
        store.restore(0, mode="bogus")


# ---------------------------------------------------------------------------
# Diff stats
# ---------------------------------------------------------------------------

def test_diff_stats_unchanged_returns_zero(store):
    (store.work_tree / "a.py").write_text("ok\n")
    cp1 = store.record(msg_index=1, label="x")
    stats = store.diff_stats(cp1.index)
    assert stats.files_changed == 0


def test_diff_stats_after_edit_reports_changes(store):
    (store.work_tree / "a.py").write_text("one\ntwo\nthree\n")
    cp1 = store.record(msg_index=1, label="x")
    (store.work_tree / "a.py").write_text("one\nTWO\nthree\n")
    stats = store.diff_stats(cp1.index)
    assert stats.files_changed == 1
    assert stats.insertions >= 1
    assert stats.deletions >= 1


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_list_returns_most_recent_first(store):
    (store.work_tree / "a.py").write_text("a\n")
    cp1 = store.record(msg_index=1, label="cp1")
    (store.work_tree / "a.py").write_text("b\n")
    cp2 = store.record(msg_index=2, label="cp2")
    (store.work_tree / "a.py").write_text("c\n")
    cp3 = store.record(msg_index=3, label="cp3")
    items = store.list_checkpoints()
    assert [c.commit for c in items] == [cp3.commit, cp2.commit, cp1.commit]


def test_list_with_limit(store):
    (store.work_tree / "a.py").write_text("a\n")
    store.record(msg_index=1, label="cp1")
    (store.work_tree / "a.py").write_text("b\n")
    store.record(msg_index=2, label="cp2")
    items = store.list_checkpoints(limit=1)
    assert len(items) == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_parse_shortstat_full():
    s = _parse_shortstat(
        " 3 files changed, 12 insertions(+), 4 deletions(-)"
    )
    assert s.files_changed == 3
    assert s.insertions == 12
    assert s.deletions == 4


def test_parse_shortstat_insertions_only():
    s = _parse_shortstat(" 1 file changed, 5 insertions(+)")
    assert s.files_changed == 1
    assert s.insertions == 5
    assert s.deletions == 0


def test_parse_shortstat_empty():
    s = _parse_shortstat("")
    assert s.files_changed == 0


# ---------------------------------------------------------------------------
# User .git in cwd is not touched
# ---------------------------------------------------------------------------

def test_user_git_in_worktree_is_untouched(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    # Make work-tree a real git repo with the user's own state
    subprocess.run(["git", "init"], cwd=work, check=True, capture_output=True)
    user_branch_file = work / "user.txt"
    user_branch_file.write_text("user content\n")
    subprocess.run(
        ["git", "-c", "user.email=u@u", "-c", "user.name=u",
         "add", "user.txt"],
        cwd=work, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=u@u", "-c", "user.name=u",
         "commit", "-m", "user commit"],
        cwd=work, check=True, capture_output=True,
    )
    user_head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Now drive checkpoints over the same work-tree
    store = CheckpointStore(
        work_tree=work, session_id="x", base_dir=tmp_path / "ck"
    )
    user_branch_file.write_text("model edit\n")
    cp1 = store.record(msg_index=1, label="modelv1")
    user_branch_file.write_text("model edit v2\n")
    store.record(msg_index=2, label="modelv2")
    store.restore(cp1.index, mode="code")
    assert user_branch_file.read_text() == "model edit\n"

    # The user's own git history must be untouched
    user_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert user_head_before == user_head_after
