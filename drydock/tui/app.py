"""Drydock TUI — a terminal coding agent surface.

A scrolling transcript of user turns, streamed assistant text, and
collapsible tool-call cards, with a prompt box at the bottom. The agent loop
runs in a worker thread and talks to the UI only through thread-safe
messages (see tui/messages.py). Nautical theme, original branding.
"""
from __future__ import annotations

import random
import time

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
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
    PromptArea,
    ToolCard,
    UserMessage,
    result_is_ok,
    summarize_inputs,
)
from drydock.tuning import system_prompt_for_model
from drydock import __version__

BANNER = (
    f"\n  ⚓  DRYDOCK  ·  v{__version__}\n"
    "  ──────────────────────────────────────────────\n"
    "  a local coding agent — your model, your machine\n"
)

# Animated "working" line: a turning-helm arc spinner + a nautical gerund +
# elapsed time + streamed-token count. Rendered in-line above the status bar.
_SPINNER = "◜◠◝◞◡◟"
_WORKING_WORDS = [
    "Battening", "Splicing", "Hoisting", "Heaving", "Trimming", "Tacking",
    "Mooring", "Charting", "Navigating", "Sounding", "Caulking", "Rigging",
    "Lashing", "Reefing", "Furling", "Unfurling", "Dredging", "Careening",
    "Provisioning", "Helming", "Ballasting", "Swabbing", "Belaying",
    "Fathoming", "Weighing anchor", "Bailing", "Voyaging", "Plumbing",
    "Hauling", "Coiling", "Keelhauling", "Berthing", "Squalling", "Yawing",
]


def _fmt_elapsed(secs: float) -> str:
    s = int(secs)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


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
    .todo-panel {
        margin: 1 0 0 2; padding: 0 1; color: #d7e6ee;
        border-left: thick #c9a227; background: #14241c;
    }
    /* One bottom-docked footer holds, top→bottom: working-line, prompt, status. */
    #footer { dock: bottom; height: auto; background: #0b1f2a; }
    #prompt {
        margin: 1 2; border: round #2e5a6b; background: #0e2731;
        height: auto; min-height: 3; max-height: 12;
    }
    #prompt:focus { border: round #58c4dc; }
    #status { height: 1; color: #5e7a88; padding: 0 2; background: #0b1f2a; }
    /* In-line activity line; height auto → 0 lines when idle (empty content). */
    #working { height: auto; color: #6fcfc0; padding: 0 2; background: #0b1f2a; }
    """

    # Text selection + clipboard copy is enabled (Textual default). The fix
    # that matters: Ctrl+C must COPY the selected text, not quit — quitting on
    # Ctrl+C made it impossible to copy an error off the screen. Quit moved to
    # Ctrl+Q. Shift+drag also works (terminal-native selection) in most emulators.
    ALLOW_SELECT = True

    # Quit is Ctrl+D (or the /quit command). NOT Ctrl+Q — that's XON/XOFF flow
    # control in many terminals and gets eaten before the app sees it. Ctrl+C is
    # freed for copy (Textual copies the selection; our action adds feedback).
    BINDINGS = [
        Binding("ctrl+c", "copy_selection", "Copy", priority=True),
        Binding("ctrl+d", "quit", "Quit", priority=True),
        Binding("ctrl+o", "toggle_tools", "Expand/collapse details"),
        # Scroll the transcript from the keyboard (focus stays on the prompt,
        # and SSH sessions often don't forward the mouse wheel). priority=True
        # so the prompt's TextArea doesn't swallow PageUp/PageDown first.
        Binding("pageup", "scroll_up", "Scroll up", priority=True, show=False),
        Binding("pagedown", "scroll_down", "Scroll down", priority=True, show=False),
        Binding("ctrl+home", "scroll_top", "Top", priority=True, show=False),
        Binding("ctrl+end", "scroll_bottom", "Bottom", priority=True, show=False),
    ]

    def action_scroll_up(self) -> None:
        self._scroll.scroll_page_up()

    def action_scroll_down(self) -> None:
        self._scroll.scroll_page_down()

    def action_scroll_top(self) -> None:
        self._scroll.scroll_home()

    def action_scroll_bottom(self) -> None:
        self._scroll.scroll_end()

    def on_key(self, event) -> None:
        """Type-anywhere: route printable keys to the prompt even if focus has
        drifted (e.g. after clicking the transcript to select text), so the user
        never has to click the input box to start typing."""
        if event.is_printable and event.character:
            prompt = self.query_one("#prompt", PromptArea)
            if not prompt.has_focus:
                prompt.focus()
                prompt.insert(event.character)
                event.prevent_default()
                event.stop()

    def action_copy_selection(self) -> None:
        """Ctrl+C: copy an in-app selection if there is one; otherwise it's the
        exit key — press it twice in a row (within ~2s) to quit, matching the
        familiar Ctrl+C-to-exit muscle memory. (Ctrl+D and /quit also exit.)"""
        try:
            selected = self.screen.get_selected_text()
        except Exception:  # noqa: BLE001
            selected = None
        if selected:
            self.copy_to_clipboard(selected)
            self.notify(f"Copied {len(selected)} chars to clipboard", timeout=2)
            self._ctrl_c_armed = False
            return
        # No selection → double-Ctrl+C to exit.
        if self._ctrl_c_armed:
            self.exit()
            return
        self._ctrl_c_armed = True
        self.notify("Press Ctrl+C again to exit", timeout=2)
        self.set_timer(2.0, self._disarm_ctrl_c)

    def _disarm_ctrl_c(self) -> None:
        self._ctrl_c_armed = False

    def __init__(self, config: dict) -> None:
        super().__init__()
        self.config = config
        self.state = AgentState()
        self.system = self._build_system(config.get("model"))
        self._current_assistant: AssistantMessage | None = None
        self._last_card: ToolCard | None = None
        self._busy = False
        self._queue: list[str] = []  # prompts submitted while a turn is running
        self._ctx_tokens = 0  # current context size (last turn's prompt tokens)
        self._ctrl_c_armed = False  # first Ctrl+C arms; second within ~2s exits
        # Live "working" line state.
        self._work_start = 0.0
        self._work_word = ""
        self._work_chars = 0   # streamed output chars this turn (→ ~tokens)
        self._spinner_i = 0
        self._todo_panel = None  # the live task-checklist widget (or None)

        # The agent (worker thread) calls this to gate a sensitive command on
        # user approval. Bridges to the UI thread and blocks until the user
        # chooses; returns "allow" / "always" / "deny".
        self.config["request_approval"] = self._request_approval

    def _request_approval(self, command: str, reason: str) -> str:
        from drydock.tui.approval import ApprovalModal

        try:
            return self.call_from_thread(
                self.push_screen_wait, ApprovalModal(command, reason)
            )
        except Exception:  # noqa: BLE001 — if the UI can't ask, fail safe to deny
            return "deny"

    def _build_system(self, model: str | None) -> str:
        # The TUI must honor AGENTS.md/DRYDOCK.md like the CLI does — it's the
        # primary surface, and project instructions carry the user's tool/style
        # conventions. cli.main() loads them into config after ensure_agents_md.
        return system_prompt_for_model(model) + self.config.get("project_instructions", "")

    # ── layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(BANNER, id="banner")
        yield VerticalScroll(id="transcript")
        with Vertical(id="footer"):
            yield Static("", id="working")  # in-line activity (empty when idle)
            yield PromptArea(id="prompt")
            yield Static(self._status_text(), id="status")

    def on_mount(self) -> None:
        prompt = self.query_one("#prompt", PromptArea)
        # Persist history across sessions when the CLI provides a path (tests
        # omit it and stay in-memory, so they never touch the real home dir).
        hist_path = self.config.get("history_path")
        if hist_path:
            from pathlib import Path

            from drydock.tui.widgets import PromptHistory

            prompt.cmd_history = PromptHistory(Path(hist_path))
        onboarding = self.config.get("onboarding")
        if onboarding:
            self._info(onboarding)
        prompt.focus()
        # Drive the animated working line (only repaints while a turn is busy).
        self.set_interval(0.18, self._tick_work)

    def _tick_work(self) -> None:
        if self._busy:
            self._spinner_i += 1
            self._refresh_status()

    def _status_text(self) -> str:
        """The persistent footer — always shows the key commands + ctx, busy or
        not (the live activity is the separate in-line #working line)."""
        model = self.config.get("model", "?")
        limit = self.config.get("context_limit", 65536) or 65536
        used = self._ctx_tokens
        pct = min(100, round(used / limit * 100)) if limit else 0
        ctx = f"ctx {used:,}/{limit // 1000}k ({pct}%)"
        flag = "⚓ working" if self._busy else "⚓ ready"
        return (
            f"{flag}  ·  {model}  ·  Ctrl+C×2 quit · PgUp/PgDn scroll · "
            f"Ctrl+O details  ·  {ctx}"
        )

    def _working_text(self) -> str:
        """The in-line activity line (empty when idle, so it takes no space)."""
        if not self._busy:
            return ""
        spin = _SPINNER[self._spinner_i % len(_SPINNER)]
        elapsed = _fmt_elapsed(time.monotonic() - self._work_start)
        toks = _fmt_tokens(self._work_chars // 4)
        effort = self.state.current_effort
        eff = f" · thinking with {effort} effort" if effort else ""
        queued = f" · {len(self._queue)} queued" if self._queue else ""
        return f"{spin} {self._work_word}…  ({elapsed} · ↓ {toks} tokens{eff}{queued})"

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())
        self.query_one("#working", Static).update(self._working_text())

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

    def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        text = event.text.strip()
        prompt = self.query_one("#prompt", PromptArea)
        prompt.clear()
        if not text:
            return
        if text.startswith("/"):
            self._handle_slash(text)  # meta commands stay out of history
            return
        prompt.cmd_history.add(text)
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
        self._work_start = time.monotonic()
        self._work_word = random.choice(_WORKING_WORDS)
        self._work_chars = 0
        self._spinner_i = 0
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
        elif cmd == "/back":
            from drydock.agent import drop_last_turn

            if drop_last_turn(self.state.messages):
                self._refresh_status()
                self._info(
                    "↩ rewound the last turn (removed it from the model's context). "
                    "Files were NOT reverted — use /undo for that."
                )
            else:
                self._info("Nothing to go back to.")
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
                "  /back            rewind the last turn from the model's context\n"
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
        self.system = self._build_system(name)  # prompt may be model-specific
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
        self._work_chars += len(m.text)
        self._scroll.scroll_end(animate=False)

    def on_agent_tool_start(self, m: AgentToolStart) -> None:
        self._current_assistant = None  # end the current text block
        if m.name == "todo":
            self._render_todo(m.inputs.get("tasks", ""))
            self._last_card = None  # tool_end is a no-op for the checklist
            return
        card = ToolCard(m.name, summarize_inputs(m.inputs))
        self._last_card = card
        self._mount(card)

    def _render_todo(self, tasks: str) -> None:
        """Render/refresh the live task checklist (Claude-Code-style). Moves to
        the bottom on each update so the current plan is always in view."""
        from rich.markup import escape

        from drydock.tools import parse_todo

        items = parse_todo(tasks)
        if not items:
            return
        glyph = {"done": "[green]✓[/]", "in_progress": "[yellow]▸[/]", "pending": "○"}
        done = sum(1 for _, s in items if s == "done")
        lines = [f"[b]Plan[/b]  [dim]({done}/{len(items)})[/]"]
        for text, status in items:
            text = escape(text)  # task text is model-supplied — don't let it inject markup
            style = "dim strike" if status == "done" else (
                "bold" if status == "in_progress" else "")
            lines.append(f"  {glyph.get(status, '○')} [{style}]{text}[/]" if style
                         else f"  {glyph.get(status, '○')} {text}")
        body = "\n".join(lines)
        if self._todo_panel is not None:
            try:
                self._todo_panel.remove()
            except Exception:  # noqa: BLE001
                pass
        self._todo_panel = Static(body, classes="todo-panel")
        self._mount(self._todo_panel)

    def on_agent_tool_end(self, m: AgentToolEnd) -> None:
        if self._last_card is not None:
            self._last_card.finish(m.result, ok=result_is_ok(m.result))
        self._scroll.scroll_end(animate=False)

    def on_agent_turn_done(self, m: AgentTurnDone) -> None:
        # The last turn's prompt tokens are the current context-window usage.
        self._ctx_tokens = m.input_tokens
        self._refresh_status()

    def on_agent_finished(self, m: AgentFinished) -> None:
        self._busy = False
        self._current_assistant = None
        if self._queue:
            self._begin(self._queue.pop(0))
            return
        self._refresh_status()
        self.query_one("#prompt", PromptArea).focus()

    def on_agent_error(self, m: AgentError) -> None:
        self._mount(ErrorMessage(m.error))


def run_tui(config: dict) -> None:
    # Mouse ON (default): the wheel scrolls the transcript and the scrollbar
    # works. Text selection is in-app — drag to select (Textual highlights it),
    # then Ctrl+C copies (no Shift needed). Shift+drag still does a terminal-
    # native selection as a fallback. You can't have BOTH wheel-scroll and
    # terminal-native plain-drag selection — the mouse can only go to one — and
    # wheel scroll is the more-missed affordance, so the app keeps the mouse.
    DrydockApp(config).run()
