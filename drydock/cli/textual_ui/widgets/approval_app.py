from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from drydock.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from drydock.cli.textual_ui.widgets.tool_widgets import get_approval_widget
from drydock.core.config import DrydockConfig


class ApprovalApp(Container):
    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("1", "select_1", "Yes", show=False),
        Binding("y", "select_1", "Yes", show=False),
        Binding("2", "select_2", "Always Tool Session", show=False),
        Binding("3", "select_3", "No", show=False),
        Binding("n", "select_3", "No", show=False),
    ]

    class ApprovalGranted(Message):
        def __init__(self, tool_name: str, tool_args: BaseModel) -> None:
            super().__init__()
            self.tool_name = tool_name
            self.tool_args = tool_args

    class ApprovalGrantedAlwaysTool(Message):
        def __init__(
            self, tool_name: str, tool_args: BaseModel, save_permanently: bool
        ) -> None:
            super().__init__()
            self.tool_name = tool_name
            self.tool_args = tool_args
            self.save_permanently = save_permanently

    class StrayKey(Message):
        """User started typing chat input while the modal was up.

        Real users (and the pexpect-based stress harness) hit this when
        an approval modal appears mid-conversation: their keystrokes get
        routed to ApprovalApp, which only binds up/down/enter/1/y/2/3/n
        and silently drops everything else. Before this fix, those
        characters disappeared into the void; the user assumed the TUI
        was ignoring them and re-typed (or gave up).

        Now we forward stray printable characters AND Enter to the main
        app, which appends them to `_pending_messages` so they replay
        into the chat input after the modal closes.
        """
        def __init__(self, text: str, is_enter: bool = False) -> None:
            super().__init__()
            self.text = text
            self.is_enter = is_enter

    class ApprovalRejected(Message):
        def __init__(self, tool_name: str, tool_args: BaseModel) -> None:
            super().__init__()
            self.tool_name = tool_name
            self.tool_args = tool_args

    def __init__(
        self, tool_name: str, tool_args: BaseModel, config: DrydockConfig
    ) -> None:
        super().__init__(id="approval-app")
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.config = config
        self.selected_option = 0
        self.content_container: Vertical | None = None
        self.title_widget: Static | None = None
        self.tool_info_container: Vertical | None = None
        self.option_widgets: list[Static] = []
        self.help_widget: Static | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-options"):
            yield NoMarkupStatic("")
            for _ in range(3):
                widget = NoMarkupStatic("", classes="approval-option")
                self.option_widgets.append(widget)
                yield widget
            yield NoMarkupStatic("")
            self.help_widget = NoMarkupStatic(
                "↑↓ navigate  Enter select  ESC reject", classes="approval-help"
            )
            yield self.help_widget

        with Vertical(id="approval-content"):
            self.title_widget = NoMarkupStatic(
                f"⚠ {self.tool_name} command", classes="approval-title"
            )
            yield self.title_widget

            with VerticalScroll(classes="approval-tool-info-scroll"):
                self.tool_info_container = Vertical(
                    classes="approval-tool-info-container"
                )
                yield self.tool_info_container

    async def on_mount(self) -> None:
        await self._update_tool_info()
        self._update_options()
        self.focus()

    async def _update_tool_info(self) -> None:
        if not self.tool_info_container:
            return

        approval_widget = get_approval_widget(self.tool_name, self.tool_args)
        await self.tool_info_container.remove_children()
        await self.tool_info_container.mount(approval_widget)

    def _update_options(self) -> None:
        options = [
            ("Yes", "yes"),
            (f"Yes and always allow {self.tool_name} for this session", "yes"),
            ("No and tell the agent what to do instead", "no"),
        ]

        for idx, ((text, color_type), widget) in enumerate(
            zip(options, self.option_widgets, strict=True)
        ):
            is_selected = idx == self.selected_option

            cursor = "› " if is_selected else "  "
            option_text = f"{cursor}{idx + 1}. {text}"

            widget.update(option_text)

            widget.remove_class("approval-cursor-selected")
            widget.remove_class("approval-option-selected")
            widget.remove_class("approval-option-yes")
            widget.remove_class("approval-option-no")

            if is_selected:
                widget.add_class("approval-cursor-selected")
                if color_type == "yes":
                    widget.add_class("approval-option-yes")
                else:
                    widget.add_class("approval-option-no")
            else:
                widget.add_class("approval-option-selected")
                if color_type == "yes":
                    widget.add_class("approval-option-yes")
                else:
                    widget.add_class("approval-option-no")

    async def on_key(self, event: events.Key) -> None:
        """Catch printable characters that aren't in BINDINGS and forward
        them to the main app's pending-messages queue instead of silently
        dropping them. Without this, a user (or pexpect harness) who types
        chat input while the modal is up loses every keystroke.

        Bound keys (up/down/enter/1/y/2/3/n) are handled by Textual's
        binding system BEFORE on_key fires for unmatched events — so we
        only ever see the stray ones here.

        We accept any single printable character. Enter is treated as a
        message-send boundary: flush the buffered stray-key text as one
        pending message. The flush boundary lives on the App side so we
        can preserve typing rhythm across multiple modals."""
        if event.character and event.character.isprintable():
            self.post_message(self.StrayKey(text=event.character, is_enter=False))
            event.stop()
        elif event.key == "enter":
            # Enter without any pending stray text means the user
            # confirmed the focused option — let the BINDINGS handle it.
            # (This on_key won't fire for "enter" if the binding consumed
            # it first.) Forward as flush marker just in case.
            self.post_message(self.StrayKey(text="", is_enter=True))

    def action_move_up(self) -> None:
        self.selected_option = (self.selected_option - 1) % 3
        self._update_options()

    def action_move_down(self) -> None:
        self.selected_option = (self.selected_option + 1) % 3
        self._update_options()

    def action_select(self) -> None:
        self._handle_selection(self.selected_option)

    def action_select_1(self) -> None:
        self.selected_option = 0
        self._handle_selection(0)

    def action_select_2(self) -> None:
        self.selected_option = 1
        self._handle_selection(1)

    def action_select_3(self) -> None:
        self.selected_option = 2
        self._handle_selection(2)

    def action_reject(self) -> None:
        self.selected_option = 2
        self._handle_selection(2)

    def _handle_selection(self, option: int) -> None:
        match option:
            case 0:
                self.post_message(
                    self.ApprovalGranted(
                        tool_name=self.tool_name, tool_args=self.tool_args
                    )
                )
            case 1:
                self.post_message(
                    self.ApprovalGrantedAlwaysTool(
                        tool_name=self.tool_name,
                        tool_args=self.tool_args,
                        save_permanently=False,
                    )
                )
            case 2:
                self.post_message(
                    self.ApprovalRejected(
                        tool_name=self.tool_name, tool_args=self.tool_args
                    )
                )

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)
