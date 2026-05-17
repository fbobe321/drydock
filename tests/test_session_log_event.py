"""Tests for SessionLogger.log_event + agent_loop.queue_user_injection
session-log integration.

The bug: when the user typed during an agent turn, queue_user_injection
recorded the message in an in-memory list but didn't write anything to
the session log until the NEXT turn boundary drained it. For long
multi-tool turns (5+ minutes), this made the on-disk session log
silently lag behind what the user had typed — debugging "did my queued
message land?" required waiting.

Fix: SessionLogger.log_event appends structured events to
<session_dir>/events.jsonl. queue_user_injection calls it with
event=user_injection_queued so the queue is visible immediately.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from drydock.core.config import SessionLoggingConfig
from drydock.core.session.session_logger import SessionLogger


# ── SessionLogger.log_event ────────────────────────────────────────────

def test_log_event_writes_jsonl():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = SessionLoggingConfig(enabled=True, save_dir=tmp)
        sl = SessionLogger(cfg, "abc12345-deadbeef")
        sl.log_event({"event": "test", "value": 42})
        sl.log_event({"event": "test2", "value": "x"})
        events_file = sl.session_dir / "events.jsonl"
        assert events_file.is_file()
        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 2
        r0 = json.loads(lines[0])
        assert r0["event"] == "test"
        assert r0["value"] == 42
        assert "ts" in r0  # auto-added timestamp


def test_log_event_no_crash_when_logging_disabled():
    cfg = SessionLoggingConfig(enabled=False, save_dir="")
    sl = SessionLogger(cfg, "test")
    # Must be a no-op, must not raise
    sl.log_event({"event": "should_be_dropped"})
    assert sl.session_dir is None


def test_log_event_creates_session_dir_if_missing():
    """Early-session events (queue_user_injection during the very first
    turn) can fire before persist_messages has materialised the dir.
    log_event must create it on demand."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = SessionLoggingConfig(enabled=True, save_dir=tmp)
        sl = SessionLogger(cfg, "newid-test")
        # session_dir was assigned in __init__ but the directory itself
        # may not exist yet (save_folder is computed, save_dir.mkdir is
        # called but session_dir is a subdir).
        sl.log_event({"event": "early"})
        assert sl.session_dir.is_dir()
        assert (sl.session_dir / "events.jsonl").is_file()


def test_log_event_silent_on_failure():
    """Disk errors / permission issues must not break the caller."""
    cfg = SessionLoggingConfig(enabled=True, save_dir="/nonexistent/path/cannot/write")
    try:
        sl = SessionLogger(cfg, "x")
    except (OSError, PermissionError):
        # If init itself fails, we can't test log_event — fine, the
        # silent-on-failure guarantee is for steady-state writes
        return
    # If init succeeded somehow, log_event must still not raise
    sl.log_event({"event": "doomed"})


# ── queue_user_injection emits the event ───────────────────────────────

def test_queue_user_injection_emits_session_event():
    """String-grep regression: agent_loop.queue_user_injection must
    call session_logger.log_event so the queue is visible in
    events.jsonl immediately, not just after the next drain."""
    import inspect
    from drydock.core.agent_loop import AgentLoop
    src = inspect.getsource(AgentLoop.queue_user_injection)
    assert "log_event" in src
    assert "user_injection_queued" in src
