"""Ephemeral workers stop the background jobs they left behind (a hung pytest that the
Bash tool auto-promoted outlived its mission worker by 7 hours)."""
from __future__ import annotations

import subprocess
import time

from drydock import jobs


def _adopted(tmp_path, cwd, start):
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    jobs.adopt(p.pid, p.pid, "sleep 60", str(cwd), start)
    return p


def test_stop_jobs_started_scopes_by_cwd_time_and_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_DIR", str(tmp_path / "jobs"))
    inside, outside = tmp_path / "wt", tmp_path / "other"
    inside.mkdir()
    outside.mkdir()
    t0 = time.time()
    mine = _adopted(tmp_path, inside / "sub", t0 + 1)
    elsewhere = _adopted(tmp_path, outside, t0 + 1)
    older = _adopted(tmp_path, inside, t0 - 100)
    detached = subprocess.Popen(["sleep", "60"], start_new_session=True)
    meta = jobs.adopt(detached.pid, detached.pid, "sleep 60", str(inside), t0 + 1)
    meta["mode"] = "detached"
    jobs._write_meta(meta)
    try:
        stopped = jobs.stop_jobs_started(str(inside), t0, adopted_only=True)
        assert len(stopped) == 1
        mine.wait(timeout=5)
        for p in (elsewhere, older, detached):
            assert p.poll() is None
        assert len(jobs.stop_jobs_started(str(inside), t0)) == 1   # now the detached one too
        detached.wait(timeout=5)
    finally:
        for p in (mine, elsewhere, older, detached):
            p.kill()
            p.wait()
