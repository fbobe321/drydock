"""Background-job registry + Bash background/auto-promote + the Jobs tool."""
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX job semantics")

from drydock import jobs


@pytest.fixture(autouse=True)
def _tmp_jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_DIR", str(tmp_path / "jobs"))


def _wait_state(jid, want, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = jobs.status(jid)
        if s and s["state"] == want:
            return s
        time.sleep(0.05)
    return jobs.status(jid)


def test_launch_background_runs_detached_records_exit_and_output():
    meta = jobs.launch_background("echo jobhello", "/tmp", shell_path=None, shell_kind="sh")
    s = _wait_state(meta["id"], "finished")
    assert s["state"] == "finished" and s["exit_code"] == 0
    assert "jobhello" in s["tail"]


def test_nonzero_exit_is_recorded():
    meta = jobs.launch_background("exit 7", "/tmp", shell_path=None, shell_kind="sh")
    s = _wait_state(meta["id"], "finished")
    assert s["exit_code"] == 7


def test_list_jobs_includes_launched():
    a = jobs.launch_background("echo one", "/tmp", shell_path=None, shell_kind="sh")
    _wait_state(a["id"], "finished")
    ids = {j["id"] for j in jobs.list_jobs()}
    assert a["id"] in ids


def test_stop_terminates_a_running_job():
    meta = jobs.launch_background("sleep 30", "/tmp", shell_path=None, shell_kind="sh")
    assert _wait_state(meta["id"], "running", 2.0)["state"] == "running"
    msg = jobs.stop(meta["id"])
    assert "SIGTERM" in msg
    # after SIGTERM the process group dies
    end = time.monotonic() + 3
    while time.monotonic() < end and jobs._alive(meta["pid"]):
        time.sleep(0.05)
    assert not jobs._alive(meta["pid"])


def test_adopt_registers_a_running_foreground_process():
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        meta = jobs.adopt(p.pid, os.getpgid(p.pid), "sleep 30", "/tmp", time.time(),
                          initial_output="captured so far\n")
        s = jobs.status(meta["id"])
        assert s["state"] == "running" and "captured so far" in s["tail"]
        assert s["mode"] == "adopted"
    finally:
        p.terminate()
        p.wait(timeout=3)


def test_status_of_missing_job_is_none():
    assert jobs.status("deadbeef") is None
    assert "No such job" in jobs.stop("deadbeef")


# ── Bash tool integration ───────────────────────────────────────────────────

def test_bash_background_flag_creates_a_job():
    import drydock.tools as T
    out = T.tool_bash({"command": "echo hi; sleep 2", "background": True}, {"cwd": "/tmp"})
    assert "background job" in out.lower()
    jid = out.split("job ")[1].split()[0].rstrip(".")
    assert jobs.status(jid) is not None


def test_bash_auto_promotes_a_long_budget_overrun(monkeypatch):
    import drydock.tools as T
    # a LONG-budget command (threshold lowered for the test) that overruns → adopted,
    # not killed. Below-threshold overruns keep the kill+retry flow (tested elsewhere).
    monkeypatch.setattr(T, "_AUTO_BG_TIMEOUT", 1)
    out = T.tool_bash({"command": "echo begin; sleep 8", "timeout": 1}, {"cwd": "/tmp", "_abort": {}})
    assert "background" in out.lower() and "job" in out.lower()
    jid = out.split("job ")[1].split()[0].rstrip(".")
    s = jobs.status(jid)
    assert s and s["state"] == "running"          # still alive — NOT killed
    assert 0 <= s["elapsed_s"] < 120              # wall-clock elapsed sane (not monotonic mixup)
    jobs.stop(jid)


def test_bash_short_timeout_overrun_still_kills_and_guides(monkeypatch):
    import drydock.tools as T
    monkeypatch.setattr(T, "_AUTO_BG_TIMEOUT", 900)   # default: short overruns kill
    out = T.tool_bash({"command": "sleep 8", "timeout": 1}, {"cwd": "/tmp", "_abort": {}})
    assert "timed out after 1s" in out                # killed, not backgrounded
    assert "background=true" in out                    # …but guided toward backgrounding


def test_jobs_tool_list_status_stop():
    import drydock.tools as T
    meta = jobs.launch_background("sleep 20", "/tmp", shell_path=None, shell_kind="sh")
    _wait_state(meta["id"], "running", 2.0)
    listing = T.tool_jobs({"action": "list"}, {})
    assert meta["id"] in listing and "running" in listing
    st = T.tool_jobs({"action": "status", "id": meta["id"]}, {})
    assert meta["id"] in st
    assert "SIGTERM" in T.tool_jobs({"action": "stop", "id": meta["id"]}, {})


def test_jobs_tool_registered_and_surfaces_for_status_question():
    import drydock.tools  # noqa: F401 — triggers register_all
    from drydock.tool_registry import schemas
    from drydock.tool_select import select_tools
    names = {s["name"] for s in schemas()}
    assert "Jobs" in names
    surfaced = {s["name"] for s in select_tools(
        schemas(), task_text="how's the training job doing in the background?", max_tools=12)}
    assert "Jobs" in surfaced


# ── no completion-notify surface at all (removed deliberately — no phone-home) ──

def test_no_completion_notify_surface(tmp_path, monkeypatch):
    # There is NO hook that runs an extra command when a background job finishes.
    # launch_background takes no notify param, and a background job must NOT run
    # anything from any env var (the old DRYDOCK_JOB_NOTIFY_CMD is dead).
    import inspect
    assert "notify" not in inspect.signature(jobs.launch_background).parameters

    import drydock.tools as T
    marker = tmp_path / "should_never_exist.txt"
    hook = tmp_path / "hook.sh"
    hook.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
    hook.chmod(0o755)
    monkeypatch.setenv("DRYDOCK_JOB_NOTIFY_CMD", f"sh {hook}")   # dead var: must be ignored
    out = T.tool_bash({"command": "echo done; exit 0", "background": True}, {"cwd": "/tmp"})
    jid = out.split("job ")[1].split()[0].rstrip(".")
    s = _wait_state(jid, "finished")
    assert s["exit_code"] == 0                # job runs + records normally
    time.sleep(0.3)
    assert not marker.exists()                # ...but nothing extra ever fired
