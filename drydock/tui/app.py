"""Drydock TUI — a terminal coding agent surface.

A scrolling transcript of user turns, streamed assistant text, and
collapsible tool-call cards, with a prompt box at the bottom. The agent loop
runs in a worker thread and talks to the UI only through thread-safe
messages (see tui/messages.py). Nautical theme, original branding.
"""
from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from drydock.agent import (
    AgentState,
    TextChunk,
    ToolEnd,
    ToolStart,
    TurnDone,
    run,
)
from drydock.tui.messages import (
    AgentError,
    AgentFinished,
    AgentText,
    AgentToolEnd,
    AgentToolStart,
    AgentTurnDone,
)
from drydock.tui.widgets import (
    AssistantMessage,
    ErrorMessage,
    PromptInput,
    ToolCard,
    UserMessage,
    result_is_ok,
    summarize_inputs,
)
from drydock.tuning import system_prompt_for_model

BANNER = r"""  ___      ___      ___
 |   \ ┌─┐ |   \    ⚓  DRYDOCK   a local coding agent
 | |) | ┌─┘ | |) |     dock · build · ship — your model, your machine
 |___/ └─┘  |___/
"""


class DrydockApp(App):
    CSS = """
    Screen { background: #0b1f2a; }
    #banner {
        color: #58c4dc; background: #0b1f2a; padding: 1 2 0 2; text-style: bold;
    }
    #transcript { padding: 0 2; }
    .user-msg {
        color: #ffd479; margin: 1 0 0 0; text-style: bold;
    }
    .assistant-msg { color: #d7e6ee; margin: 0 0 0 2; }
    .error-msg { color: #ff6b6b; margin: 1 0; }
    .tool-card {
        margin: 0 0 0 2; border-left: thick #2e5a6b; background: #0e2731;
    }
    .tool-card.ok { border-left: thick #2e8b6b; }
    .tool-card.fail { border-left: thick #b3503e; }
    .tool-body { color: #9bb4c0; padding: 0 1; }
    #prompt {
        dock: bottom; margin: 1 2; border: round #2e5a6b; background: #0e2731;
    }
    #status {
        dock: bottom; height: 1; color: #5e7a88; padding: 0 2; background: #0b1f2a;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+o", "toggle_tools", "Expand/collapse tools"),
    ]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self.config = config
        self.state = AgentState()
        self.system = system_prompt_for_model(config.get("model"))
        self._current_assistant: AssistantMessage | None = None
        self._last_card: ToolCard | None = None
        self._busy = False
        self._queue: list[str] = []  # prompts submitted while a turn is running

    # ── layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(BANNER, id="banner")
        yield VerticalScroll(id="transcript")
        yield Static(self._status_text(), id="status")
        yield PromptInput(placeholder="Type a task and press Enter… (/help)", id="prompt")

    def on_mount(self) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        # Persist history across sessions when the CLI provides a path (tests
        # omit it and stay in-memory, so they never touch the real home dir).
        hist_path = self.config.get("history_path")
        if hist_path:
            from pathlib import Path

            from drydock.tui.widgets import PromptHistory

            prompt.history = PromptHistory(Path(hist_path))
        onboarding = self.config.get("onboarding")
        if onboarding:
            self._info(onboarding)
        prompt.focus()

    def _status_text(self) -> str:
        model = self.config.get("model", "?")
        toks = f"{self.state.total_input_tokens}in/{self.state.total_output_tokens}out"
        flag = "⏳ working" if self._busy else "⚓ ready"
        queued = f"  ·  {len(self._queue)} queued" if self._queue else ""
        return (
            f"{flag}  ·  model: {model}  ·  {toks}{queued}"
            f"  ·  Ctrl+O details · Ctrl+C quit"
        )

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())

    @property
    def _scroll(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _mount(self, widget) -> None:
        self._scroll.mount(widget)
        self._scroll.scroll_end(animate=False)

    def _ensure_assistant(self) -> AssistantMessage:
        if self._current_assistant is None:
            self._current_assistant = AssistantMessage()
            self._mount(self._current_assistant)
        return self._current_assistant

    # ── input ─────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: PromptInput.Submitted) -> None:
        text = event.value.strip()
        prompt = self.query_one("#prompt", PromptInput)
        prompt.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_slash(text)  # meta commands stay out of history
            return
        prompt.history.add(text)
        # Show the user turn immediately, in order. If a turn is already
        # running, queue this one and drain it when the current turn finishes
        # instead of dropping it on the floor.
        self._mount(UserMessage(text))
        if self._busy:
            self._queue.append(text)
            self._refresh_status()
            return
        self._begin(text)

    def _begin(self, text: str) -> None:
        """Start an agent turn for an already-displayed user prompt."""
        self._current_assistant = None
        self._busy = True
        self._refresh_status()
        self._run_agent(text)

    def _info(self, text: str) -> None:
        # markup=False so bracketed help/paths (e.g. "/model [name]") render literally.
        self._mount(Static(text, classes="assistant-msg", markup=False))

    def _handle_slash(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("/quit", "/exit", "/q"):
            self.exit()
        elif cmd == "/clear":
            self.state = AgentState()
            self._scroll.remove_children()
            self._current_assistant = None
            self._refresh_status()
        elif cmd == "/model":
            self._cmd_model(arg)
        elif cmd == "/cwd":
            self._cmd_cwd(arg)
        elif cmd == "/undo":
            from drydock.tools import undo_last

            self._info(undo_last(self.config))
        elif cmd == "/status":
            t = self.state
            self._info(
                f"model: {self.config.get('model')}  ·  cwd: {self.config.get('cwd')}\n"
                f"turns: {t.turn_count}  ·  messages: {len(t.messages)}  ·  "
                f"tokens: {t.total_input_tokens}in/{t.total_output_tokens}out"
            )
        elif cmd == "/help":
            self._info(
                "Commands:\n"
                "  /help            this help\n"
                "  /model [name]    show or switch the model\n"
                "  /cwd [path]      show or change the working directory\n"
                "  /undo            revert the last file write/edit\n"
                "  /status          session model, cwd, turns, tokens\n"
                "  /clear           reset the conversation\n"
                "  /quit            exit\n"
                "Type a task and press Enter. ↑/↓ recall history · Ctrl+O expands tools."
            )
        else:
            self._mount(ErrorMessage(f"unknown command: {cmd} (try /help)"))

    def _cmd_model(self, name: str) -> None:
        if not name:
            self._info(f"model: {self.config.get('model')}")
            return
        self.config["model"] = name
        self.system = system_prompt_for_model(name)  # prompt may be model-specific
        self._refresh_status()
        self._info(f"switched model → {name}")

    def _cmd_cwd(self, path: str) -> None:
        if not path:
            self._info(f"cwd: {self.config.get('cwd')}")
            return
        from pathlib import Path

        target = Path(path).expanduser()
        if not target.is_absolute():
            target = Path(self.config.get("cwd", ".")) / target
        if not target.is_dir():
            self._mount(ErrorMessage(f"not a directory: {target}"))
            return
        self.config["cwd"] = str(target.resolve())
        self._info(f"cwd → {self.config['cwd']}")

    def action_toggle_tools(self) -> None:
        cards = self.query(ToolCard)
        any_collapsed = any(c.collapsed for c in cards)
        for c in cards:
            c.collapsed = not any_collapsed

    # ── agent worker (thread) ─────────────────────────────────────────────

    @work(thread=True, exclusive=True)
    def _run_agent(self, text: str) -> None:
        try:
            for ev in run(text, self.state, self.config, self.system):
                if isinstance(ev, TextChunk):
                    self.post_message(AgentText(ev.text))
                elif isinstance(ev, ToolStart):
                    self.post_message(AgentToolStart(ev.name, ev.inputs))
                elif isinstance(ev, ToolEnd):
                    self.post_message(AgentToolEnd(ev.name, ev.result))
                elif isinstance(ev, TurnDone):
                    self.post_message(AgentTurnDone(ev.input_tokens, ev.output_tokens))
        except Exception as e:  # noqa: BLE001 — surface any agent error to the UI
            self.post_message(AgentError(str(e)))
        finally:
            self.post_message(AgentFinished())

    # ── agent → UI handlers ───────────────────────────────────────────────

    def on_agent_text(self, m: AgentText) -> None:
        self._ensure_assistant().append(m.text)
        self._scroll.scroll_end(animate=False)

    def on_agent_tool_start(self, m: AgentToolStart) -> None:
        self._current_assistant = None  # end the current text block
        card = ToolCard(m.name, summarize_inputs(m.inputs))
        self._last_card = card
        self._mount(card)

    def on_agent_tool_end(self, m: AgentToolEnd) -> None:
        if self._last_card is not None:
            self._last_card.finish(m.result, ok=result_is_ok(m.result))
        self._scroll.scroll_end(animate=False)

    def on_agent_turn_done(self, m: AgentTurnDone) -> None:
        self._refresh_status()

    def on_agent_finished(self, m: AgentFinished) -> None:
        self._busy = False
        self._current_assistant = None
        if self._queue:
            self._begin(self._queue.pop(0))
            return
        self._refresh_status()
        self.query_one("#prompt", PromptInput).focus()

    def on_agent_error(self, m: AgentError) -> None:
        self._mount(ErrorMessage(m.error))


def run_tui(config: dict) -> None:
    DrydockApp(config).run()
