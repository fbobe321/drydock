"""Drydock TUI — a terminal coding agent surface.

A scrolling transcript of user turns, streamed assistant text, and
collapsible tool-call cards, with a prompt box at the bottom. The agent loop
runs in a worker thread and talks to the UI only through thread-safe
messages (see tui/messages.py). Nautical theme, original branding.
"""
from __future__ import annotations

import random
import threading
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
    /* Pinned task checklist in the footer (height auto → 0 lines when empty). */
    #todo {
        height: auto; margin: 0 2 1 2; padding: 0 1; color: #d7e6ee;
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
        # Escape stops a running turn (the agent ends cleanly, session kept) —
        # the way out of a runaway task without quitting the whole app. "/stop"
        # does the same. priority so it isn't swallowed by the focused prompt.
        Binding("escape", "stop", "Stop the running turn", priority=True),
        Binding("ctrl+o", "toggle_tools", "Expand/collapse details"),
        # Scroll the transcript from the keyboard (focus stays on the prompt,
        # and SSH sessions often don't forward the mouse wheel). priority=True
        # so the prompt's TextArea doesn't swallow PageUp/PageDown first.
        Binding("pageup", "scroll_up", "Scroll up", priority=True, show=False),
        Binding("pagedown", "scroll_down", "Scroll down", priority=True, show=False),
        Binding("ctrl+home", "scroll_top", "Top", priority=True, show=False),
        Binding("ctrl+end", "scroll_bottom", "Bottom", priority=True, show=False),
    ]

    def action_stop(self) -> None:
        """STOP the running turn: signal the agent loop to end at its next safe
        point, drop any queued prompts, and hand control back. The session
        (history, files) is preserved — this is not a quit."""
        if not self._busy:
            return
        self._cancel.set()
        dropped = len(self._queue)
        self._queue.clear()
        note = "⏹ stopping after the current step…"
        if dropped:
            note += f" (discarded {dropped} queued)"
        self._info(note)
        self._refresh_status()

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
        # STOP signal: Escape / "/stop" sets it; the agent loop checks it at safe
        # points and ends the turn cleanly (session preserved). Lives in config
        # so run() — and any sub-agent that copies config — can see it.
        self._cancel = threading.Event()
        self.config["_cancel"] = self._cancel
        self._queue: list[str] = []  # prompts submitted while a turn is running
        self._ctx_tokens = 0  # current context size (last turn's prompt tokens)
        self._ctrl_c_armed = False  # first Ctrl+C arms; second within ~2s exits
        # Live "working" line state.
        self._work_start = 0.0
        self._work_word = ""
        self._work_chars = 0   # streamed output chars this turn (→ ~tokens)
        self._spinner_i = 0

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
        # The TUI honors a user's OWN AGENTS.md/DRYDOCK.md like the CLI does —
        # cli.main() loads it into config["project_instructions"] as background
        # context (drydock never auto-creates one).
        return system_prompt_for_model(model) + self.config.get("project_instructions", "")

    # ── layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(BANNER, id="banner")
        yield VerticalScroll(id="transcript")
        with Vertical(id="footer"):
            yield Static("", id="todo")     # pinned task checklist (empty = hidden)
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
        self._cancel.clear()  # fresh turn — clear any prior STOP
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
        elif cmd in ("/stop", "/cancel"):
            if self._busy:
                self.action_stop()
            else:
                self._info("Nothing is running.")
        elif cmd == "/clear":
            self.state = AgentState()
            self._scroll.remove_children()
            self._current_assistant = None
            self.config.pop("_todo", None)
            self.query_one("#todo", Static).update("")  # clear the pinned plan
            self._ctx_tokens = 0  # reset the context gauge — history is empty now
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
                "  /model           show/set model, provider, endpoint URL\n"
                "                   /model <name> · /model url <url> · /model provider <p>\n"
                "  /cwd [path]      show or change the working directory\n"
                "  /undo            revert the last file write/edit\n"
                "  /back            rewind the last turn from the model's context\n"
                "  /stop            stop the running turn (or press Esc)\n"
                "  /status          session model, cwd, turns, tokens\n"
                "  /clear           reset the conversation\n"
                "  /quit            exit\n"
                "Type a task and press Enter. ↑/↓ recall history · Esc stops · Ctrl+O expands tools."
            )
        else:
            self._mount(ErrorMessage(f"unknown command: {cmd} (try /help)"))

    def _persist_config(self) -> None:
        """Save the persistable settings (model/provider/base_url/… — save_file
        filters to those) to ~/.drydock/config.toml so setup survives restart."""
        from drydock import config as cfgmod

        ok = cfgmod.save_file(self.config, cfgmod.default_config_path())
        if not ok:
            self._mount(ErrorMessage("could not write ~/.drydock/config.toml"))

    def _cmd_model(self, arg: str) -> None:
        """Model + endpoint setup. Subcommands persist so they survive restart:
          /model                      show model, provider, endpoint
          /model <name>               set the model name
          /model url <base_url>       set the server URL (e.g. http://host:8000/v1)
          /model provider <name>      vllm | ollama | lmstudio | openai
        """
        arg = (arg or "").strip()
        if not arg:
            prov = self.config.get("provider") or "(default)"
            url = self.config.get("base_url") or "(provider default)"
            self._info(
                f"model:    {self.config.get('model')}\n"
                f"provider: {prov}\n"
                f"endpoint: {url}\n"
                "Set up:  /model <name>  ·  /model url <http://host:port/v1>  ·  "
                "/model provider <vllm|ollama|lmstudio|openai>"
            )
            return
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower()
        val = parts[1].strip() if len(parts) > 1 else ""
        if sub == "url":
            if not val:
                self._info("usage: /model url <http://host:port/v1>")
                return
            self.config["base_url"] = val
            self._persist_config()
            self._info(f"endpoint → {val}  (saved). Send a prompt to test it.")
            return
        if sub == "provider":
            from drydock.providers import PROVIDERS

            if val not in PROVIDERS:
                self._info(f"unknown provider {val!r}. Choose: {', '.join(PROVIDERS)}")
                return
            self.config["provider"] = val
            self._persist_config()
            self._info(f"provider → {val}  (saved)")
            return
        # Otherwise: set the model name (the whole arg, so names with spaces work).
        self.config["model"] = arg
        self.system = self._build_system(arg)  # prompt may be model-specific
        self._persist_config()
        self._refresh_status()
        self._info(f"model → {arg}  (saved)")

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
        """Update the PINNED task checklist (Claude-Code-style). It lives in the
        footer above the prompt, so it stays in view as the transcript scrolls —
        not mounted inline where new tool cards push it off-screen."""
        from rich.markup import escape

        from drydock.tools import parse_todo

        items = parse_todo(tasks)
        panel = self.query_one("#todo", Static)
        if not items:
            panel.update("")  # collapses to 0 height
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
        panel.update("\n".join(lines))

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
