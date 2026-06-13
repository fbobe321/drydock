"""Approval modal for sensitive shell commands.

Sensitive-but-legitimate commands (sudo, package installs, network fetches)
run only after the user okays them. This is a real ModalScreen driven by
push_screen_wait, so focus returns to the prompt automatically when it closes —
avoiding the v2 bug where a hand-rolled approval overlay left chat input dead.

The agent runs in a worker thread; DrydockApp.request_approval() bridges to the
UI thread via call_from_thread and blocks the tool until the user chooses
Allow / Always / Deny.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ApprovalModal(ModalScreen[str]):
    """Asks the user to approve a command. Dismisses with allow/always/deny."""

    CSS = """
    ApprovalModal { align: center middle; }
    #approval-box {
        width: 80; max-width: 90%; height: auto; padding: 1 2;
        background: #0e2731; border: round #2e5a6b;
    }
    #approval-title { color: #ffd479; text-style: bold; }
    #approval-reason { color: #9bb4c0; margin: 0 0 1 0; }
    #approval-cmd {
        color: #d7e6ee; background: #0b1f2a; padding: 0 1; margin: 1 0;
        border: round #2e5a6b;
    }
    #approval-buttons { height: auto; align: center middle; }
    #approval-buttons Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("y", "allow", "Allow"),
        Binding("a", "always", "Always"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    def __init__(self, command: str, reason: str) -> None:
        super().__init__()
        self._command = command
        self._reason = reason

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-box"):
            yield Static("⚓ Approve command?", id="approval-title")
            yield Static(f"This command {self._reason}.", id="approval-reason")
            yield Static(self._command.strip(), id="approval-cmd", markup=False)
            with Horizontal(id="approval-buttons"):
                yield Button("Allow (y)", variant="success", id="allow")
                yield Button("Always (a)", variant="primary", id="always")
                yield Button("Deny (n)", variant="error", id="deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("deny")
