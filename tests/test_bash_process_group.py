"""STOP must kill a Bash command's WHOLE process tree, not just the /bin/sh shell.

Regression: a command that spawns children (e.g. a brute-force script calling 7z
per word) launched the shell without a process group, so proc.kill() killed only
the shell — the orphaned children kept running AND kept the stdout pipe open, so
the follow-up communicate() blocked forever and the TUI froze on "working".
Fix: launch with start_new_session=True and kill_process_group() the whole group.
"""
from __future__ import annotations

import os
import subprocess
import time

from drydock.tools import kill_process_group


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _spawn_tree(cmd: str):
    return subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )


def test_kill_process_group_kills_children():
    # shell backgrounds two children then waits — mirrors a script spawning subprocs
    proc = _spawn_tree("sleep 53 & sleep 53 & echo up && wait")
    time.sleep(0.6)
    pgid = os.getpgid(proc.pid)
    assert _group_alive(pgid)
    kill_process_group(proc)
    try:
        proc.communicate(timeout=3)  # reap the shell
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.3)
    assert not _group_alive(pgid), "process group survived kill_process_group"


def test_kill_process_group_none_is_noop():
    # must not raise on a missing handle
    kill_process_group(None)


def test_kill_process_group_already_dead_is_safe():
    proc = _spawn_tree("true")
    proc.communicate(timeout=3)
    # process already exited — killing its group must not raise
    kill_process_group(proc)
