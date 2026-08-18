"""Background job registry — let a long-running command (ML training, a big
build, a days-long crack) run DETACHED while the agent releases the prompt, then
be checked on later.

Two ways a job gets here (both wired in tool_bash):
  * DETACHED — the model ran Bash with background=true. Launched in its own
    session with output → a log file and an exit-code marker, so it survives across
    turns AND drydock restarts.
  * ADOPTED — a foreground command overran its timeout and was auto-promoted
    instead of killed. It keeps running; its pipe is drained to the log by the
    Bash reader daemon, so it survives for the drydock session.

Registry: ~/.drydock/jobs/<id>/  (meta.json + log [+ exit]). Status is derived
live (pid liveness + the exit marker + a tail of the log), so nothing needs to be
polled or kept in memory.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
import uuid

_IS_WINDOWS = os.name == "nt"
JOBS_DIR = os.path.expanduser("~/.drydock/jobs")


def _dir(jid: str) -> str:
    return os.path.join(JOBS_DIR, str(jid))


def _meta_path(jid: str) -> str:
    return os.path.join(_dir(jid), "meta.json")


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _write_meta(meta: dict) -> None:
    os.makedirs(_dir(meta["id"]), exist_ok=True)
    with open(_meta_path(meta["id"]), "w") as f:
        json.dump(meta, f)


def _read_meta(jid: str) -> dict | None:
    try:
        with open(_meta_path(jid)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists but not ours → alive
    # It exists — but a detached job we launched (and never wait()ed on) becomes a
    # ZOMBIE when it ends, and os.kill(pid,0) still "sees" it. Reap it if it's our
    # child, and treat a zombie as dead so a stopped/finished job isn't "running".
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(") ", 1)[1].split(" ", 1)[0]
        if state == "Z":
            return False
    except OSError:
        pass
    return True


def _exit_code(meta: dict) -> int | None:
    ef = meta.get("exit_file")
    if ef and os.path.exists(ef):
        try:
            return int((open(ef).read().strip() or "-1"))
        except ValueError:
            return None
    return None


def _tail(log: str | None, n_bytes: int = 4000) -> str:
    if not log:
        return ""
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            data = f.read().decode("utf-8", "replace")
        return ("[... earlier output truncated ...]\n" + data) if size > n_bytes else data
    except OSError:
        return ""


def launch_background(cmd: str, cwd: str | None, *, shell_path: str | None,
                      shell_kind: str) -> dict:
    """Start `cmd` DETACHED; return its meta. Output → the job log; on POSIX the
    command is wrapped so its exit code is recorded when it finishes (even though
    we never wait on it)."""
    jid = _new_id()
    d = _dir(jid)
    os.makedirs(d, exist_ok=True)
    log = os.path.join(d, "log")
    exit_file: str | None = os.path.join(d, "exit")

    # Run the command in a SUBSHELL ( … ) so its own `exit N` only exits the
    # subshell — the marker line still runs and records the real exit code.
    def _wrap(q):
        return f"(\n{cmd}\n)\n__rc=$?; printf '%s' \"$__rc\" > {q}; exit $__rc"

    if shell_kind == "bash" and shell_path and not _IS_WINDOWS:
        argv, use_shell = [shell_path, "-c", _wrap(shlex.quote(exit_file))], False
    elif not _IS_WINDOWS:                                  # POSIX default shell
        argv, use_shell = _wrap(shlex.quote(exit_file)), True
    else:                                                  # Windows: best-effort, no marker
        argv, use_shell, exit_file = cmd, True, None

    logf = open(log, "wb")
    try:
        proc = subprocess.Popen(
            argv, shell=use_shell, cwd=cwd,
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=not _IS_WINDOWS,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    finally:
        logf.close()          # the child dup'd the fd; parent's copy isn't needed
    meta = {"id": jid, "command": cmd, "cwd": cwd, "pid": proc.pid,
            "pgid": _pgid(proc.pid), "log": log, "exit_file": exit_file,
            "start": time.time(), "mode": "detached"}
    _write_meta(meta)
    return meta


def adopt(pid: int, pgid: int | None, cmd: str, cwd: str | None, start: float,
          initial_output: str = "") -> dict:
    """Register an already-running foreground process (auto-promoted on timeout).
    Writes the output captured so far to the job log and returns the meta; the
    caller keeps draining the process's pipe INTO this log so it can't block."""
    jid = _new_id()
    d = _dir(jid)
    os.makedirs(d, exist_ok=True)
    log = os.path.join(d, "log")
    if initial_output:
        try:
            with open(log, "w") as f:
                f.write(initial_output)
        except OSError:
            pass
    meta = {"id": jid, "command": cmd, "cwd": cwd, "pid": pid, "pgid": pgid,
            "log": log, "exit_file": None, "start": start, "mode": "adopted"}
    _write_meta(meta)
    return meta


def status(jid: str) -> dict | None:
    meta = _read_meta(jid)
    if not meta:
        return None
    code = _exit_code(meta)
    alive = _alive(meta.get("pid"))
    if code is not None:
        state = "finished"
    elif alive:
        state = "running"
    else:
        state = "ended"       # gone, no exit marker (adopted job, or killed)
    return {**meta, "state": state, "exit_code": code,
            "elapsed_s": round(time.time() - meta["start"], 1),
            "tail": _tail(meta.get("log"))}


def list_jobs() -> list:
    if not os.path.isdir(JOBS_DIR):
        return []
    out = []
    for jid in os.listdir(JOBS_DIR):
        s = status(jid)
        if s:
            out.append(s)
    out.sort(key=lambda s: s["start"], reverse=True)
    return out


def stop(jid: str) -> str:
    meta = _read_meta(jid)
    if not meta:
        return f"No such job {jid}."
    pgid, pid = meta.get("pgid"), meta.get("pid")
    if not _alive(pid):
        s = status(jid)
        return f"Job {jid} is not running (already {s['state'] if s else 'gone'})."
    try:
        if pgid and not _IS_WINDOWS:
            os.killpg(pgid, signal.SIGTERM)
        elif pid:
            os.kill(pid, signal.SIGTERM)
    except OSError as e:
        return f"Job {jid}: could not stop ({e})."
    return f"Sent SIGTERM to job {jid}: {meta['command'][:80]}"
