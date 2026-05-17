"""Regression tests for the approval-modal-eats-keystrokes fix.

Bug: when ApprovalApp had focus, any printable character that wasn't
in its BINDINGS (up/down/enter/1/y/2/3/n) was silently swallowed.
Real users typing chat input mid-modal lost every keystroke. The
stress harness surfaced it as a 30% SKIP rate ("TUI did not accept
after 3 retries") because pexpect can't see the modal and just
types — the modal ate the prompt, harness re-tried 3x, gave up.

Fix: ApprovalApp.on_key catches stray printable chars and forwards
them to the main app via a StrayKey message. The main app buffers
them and either:
  - Flushes the buffer on Enter (treats it as a queued chat input)
  - Flushes on modal close (approval granted / always-tool / rejected)
into `_pending_messages` so they replay after the modal closes.
"""
from __future__ import annotations

import inspect

from drydock.cli.textual_ui.widgets.approval_app import ApprovalApp


# ── ApprovalApp must expose StrayKey + on_key ───────────────────────────

def test_stray_key_message_class_exists():
    """ApprovalApp.StrayKey is the message contract the main app
    listens for. Must accept text + is_enter."""
    cls = getattr(ApprovalApp, "StrayKey", None)
    assert cls is not None
    msg = cls(text="abc", is_enter=False)
    assert msg.text == "abc"
    assert msg.is_enter is False
    msg2 = cls(text="", is_enter=True)
    assert msg2.is_enter is True


def test_on_key_handler_exists_and_is_async():
    """Without on_key, the modal's BINDINGS still swallow everything
    not in {up,down,enter,1,y,2,3,n}. on_key must catch the rest."""
    method = getattr(ApprovalApp, "on_key", None)
    assert method is not None
    assert inspect.iscoroutinefunction(method)


def test_on_key_source_forwards_printable_chars():
    """String-grep regression — catches future refactors that remove
    the StrayKey forwarding."""
    src = inspect.getsource(ApprovalApp.on_key)
    assert "StrayKey" in src
    assert "post_message" in src
    assert "isprintable" in src


# ── Main app handler ────────────────────────────────────────────────────

def test_main_app_has_stray_key_handler():
    """app.py must implement on_approval_app_stray_key — Textual's
    automatic event routing relies on the snake_case naming
    convention `on_<sender_class>_<message_class>`."""
    from drydock.cli.textual_ui.app import DrydockApp
    method = getattr(DrydockApp, "on_approval_app_stray_key", None)
    assert method is not None
    assert inspect.iscoroutinefunction(method)


def test_main_app_flushes_buffer_on_approval_close():
    """All three approval-close paths (granted / always-tool / rejected)
    must call _flush_stray_key_buffer so chars typed during the modal
    aren't lost even if the user didn't press Enter."""
    from drydock.cli.textual_ui.app import DrydockApp
    for handler_name in (
        "on_approval_app_approval_granted",
        "on_approval_app_approval_granted_always_tool",
        "on_approval_app_approval_rejected",
    ):
        handler = getattr(DrydockApp, handler_name, None)
        assert handler is not None
        src = inspect.getsource(handler)
        assert "_flush_stray_key_buffer" in src, (
            f"{handler_name} must call _flush_stray_key_buffer "
            f"so the stray-key buffer doesn't leak across modals"
        )


def test_flush_method_exists():
    from drydock.cli.textual_ui.app import DrydockApp
    method = getattr(DrydockApp, "_flush_stray_key_buffer", None)
    assert method is not None
    src = inspect.getsource(method)
    assert "_stray_key_buffer" in src
    assert "_pending_messages" in src
