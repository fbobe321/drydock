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
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from drydock.agent import (
    AgentState,
    ReasoningChunk,
    TextChunk,
    ToolEnd,
    ToolStart,
    TurnDone,
    run,
)
from drydock.tui.messages import (
    AgentError,
    AgentFinished,
    AgentReasoning,
    AgentText,
    AgentToolEnd,
    AgentToolStart,
    AgentTurnDone,
)
from drydock.tui.widgets import (
    SLASH_COMMANDS,
    AssistantMessage,
    ErrorMessage,
    PromptArea,
    ReasoningCard,
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
# No agent event for this long → the activity line warns the model may be stalled
# (the gemma llama.cpp server can hang mid-generation on hard prompts). Advisory
# only. Set above the longest LEGIT silent stretch seen (a ~135s high-effort
# think before the first tool call) so normal slow turns don't nag; observed
# stalls ran 3–5 min+, so 180s still flags them well before the 30-min timeout.
_STALL_HINT_SECS = 180
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


def is_slash_command(text: str) -> bool:
    """True only for a real ``/command``, NOT a message that merely starts with a
    path — e.g. ``/app/env.py defines ...`` or ``/report.tex``. A command name is a
    single leading token of word chars (no path separators, no dots); anything with
    ``/``, ``\\`` or ``.`` in that first token is treated as a normal agent message."""
    if not text.startswith("/"):
        return False
    head = text[1:].split(maxsplit=1)[0] if len(text) > 1 else ""
    return bool(head) and not any(c in head for c in "/\\.")


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
    .reasoning-card {
        margin: 0 0 0 2; border-left: thick #6a5acd; background: #14132a;
    }
    .reasoning-body { color: #9a93c0; padding: 0 1; }
    /* Pinned task checklist in the footer (height auto → 0 lines when empty). */
    #todo {
        height: auto; margin: 0 2 1 2; padding: 0 1; color: #d7e6ee;
        border-left: thick #c9a227; background: #14241c;
    }
    /* Dimmed recommended-next-command hint (empty → 0 lines). */
    #suggest { height: auto; margin: 0 3; color: #4a6b78; text-style: italic; }
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
        Binding("ctrl+n", "use_suggestion", "Use suggested next command", show=False),
        # Scroll the transcript from the keyboard (focus stays on the prompt,
        # and SSH sessions often don't forward the mouse wheel). priority=True
        # so the prompt's TextArea doesn't swallow PageUp/PageDown first.
        Binding("pageup", "scroll_up", "Scroll up", priority=True, show=False),
        Binding("pagedown", "scroll_down", "Scroll down", priority=True, show=False),
        Binding("ctrl+home", "scroll_top", "Top", priority=True, show=False),
        Binding("ctrl+end", "scroll_bottom", "Bottom", priority=True, show=False),
    ]

    def action_stop(self) -> None:
        """STOP the running turn NOW (Esc or, while busy, a single Ctrl+C):
        signal the loop AND forcibly abort the in-flight LLM call and any
        running command so it stops immediately rather than after the current
        step. Session (history, files) is preserved — this is not a quit."""
        if not self._busy:
            return
        self._cancel.set()
        self._abort_inflight()
        dropped = len(self._queue)
        self._queue.clear()
        looping = self._repeat is not None
        self._repeat = None  # Esc/stop also ends an active /loop
        note = "⏹ stopped." + (f" (discarded {dropped} queued)" if dropped else "")
        note += " loop ended." if looping else ""
        self._info(note)
        self._refresh_status()

    def _abort_inflight(self) -> None:
        """Forcibly interrupt blocking work so STOP is immediate: close the LLM
        HTTP client (aborts a long decode) and kill any running command. The
        handles live in the shared config["_abort"] holder (a mutable dict that
        survives run()'s dict(config) copy); missing ones just mean nothing is
        in that state right now."""
        abort = self.config.get("_abort") or {}
        client = abort.get("client")
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        proc = abort.get("proc")
        if proc is not None:
            # kill the whole process group, not just the shell — else a command's
            # child processes survive STOP and keep the stdout pipe open, hanging
            # the bash tool's communicate() and freezing the TUI on "working".
            from drydock.tools import kill_process_group
            kill_process_group(proc)

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
        # While a turn is running, a single Ctrl+C STOPS it (like Esc) — the
        # interrupt key when you need to halt the TUI mid-task.
        if self._busy:
            self.action_stop()
            return
        # Idle, no selection → double-Ctrl+C to exit.
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
        if config.get("event_log", True):
            from drydock.events import EventLog, default_event_log_path
            self.state.events = EventLog(config.get("event_log_path") or default_event_log_path())
        self.system = self._build_system(config.get("model"))
        from drydock.skills import load_skills
        self._skills = load_skills(config.get("cwd") or ".")
        self._current_assistant: AssistantMessage | None = None
        self._last_card: ToolCard | None = None
        # Recommended-next-command hint state.
        self._turn_tools: set[str] = set()   # tool names used this turn
        self._turn_error = False
        self._plan_remaining = False
        self._suggestion = ""
        import os
        self._in_git = os.path.isdir(os.path.join(config.get("cwd") or ".", ".git"))
        self._busy = False
        # STOP signal: Escape / "/stop" sets it; the agent loop checks it at safe
        # points and ends the turn cleanly (session preserved). Lives in config
        # so run() — and any sub-agent that copies config — can see it.
        self._cancel = threading.Event()
        self.config["_cancel"] = self._cancel
        # Shared abort-handle holder: the provider stashes the live LLM client
        # and the bash tool its subprocess here, so STOP can force-abort them.
        # A plain dict survives run()'s dict(config) shallow copy by reference.
        self.config["_abort"] = {}
        self._queue: list[str] = []  # prompts submitted while a turn is running
        self._repeat: dict | None = None  # active /loop: {prompt, remaining, total}
        self._ctx_tokens = 0  # current context size (last turn's prompt tokens)
        self._ctrl_c_armed = False  # first Ctrl+C arms; second within ~2s exits
        # Live "working" line state.
        self._work_start = 0.0
        self._work_word = ""
        self._work_chars = 0   # streamed output chars this turn (→ ~tokens)
        self._last_progress = 0.0  # monotonic time of the last agent event (stall watchdog)
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
            yield Static("", id="suggest")  # dimmed recommended-next-command hint
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
        # Busy → show the interrupt keys; idle → the quit hint.
        keys = "Esc/Ctrl+C stop" if self._busy else "Ctrl+C×2 quit"
        # Show the task phase once it's past the default (understand).
        ph = getattr(self.state, "task", None)
        phase = f"  ·  {ph.phase}" if (ph and ph.is_set() and ph.phase != "understand") else ""
        return (
            f"{flag}  ·  {model}{phase}  ·  {keys} · PgUp/PgDn scroll · "
            f"Ctrl+O details  ·  {ctx}"
        )

    def _working_text(self) -> str:
        """The in-line activity line (empty when idle, so it takes no space)."""
        if not self._busy:
            return ""
        spin = _SPINNER[self._spinner_i % len(_SPINNER)]
        elapsed = _fmt_elapsed(time.monotonic() - self._work_start)
        # Session output tokens (updated each completed turn) + this turn's live
        # streamed chars. Tool turns are non-streaming, so _work_chars stays 0 —
        # showing the cumulative total keeps the counter meaningful instead of
        # stuck at "0 tokens" through a long multi-step task.
        toks = _fmt_tokens(self.state.total_output_tokens + self._work_chars // 4)
        effort = self.state.current_effort
        eff = f" · thinking with {effort} effort" if effort else ""
        queued = f" · {len(self._queue)} queued" if self._queue else ""
        # Stall watchdog (advisory): if no agent event has arrived for a while,
        # the model server may be hung. Flag it so the user isn't left guessing
        # whether it's thinking or stalled — but never act (Esc is theirs).
        silent = time.monotonic() - self._last_progress if self._last_progress else 0.0
        stall = (f"  ⚠ no output for {int(silent)}s — the model may be slow or stalled; "
                 "Esc to stop") if silent > _STALL_HINT_SECS else ""
        return f"{spin} {self._work_word}…  ({elapsed} · ↓ {toks} tokens{eff}{queued}){stall}"

    def _refresh_status(self) -> None:
        # The 0.18s _tick_work timer can fire one last time DURING app teardown,
        # after the footer widgets have been removed — query_one would then raise
        # NoMatches and crash the app (or fail a test). Be defensive: if the
        # widgets aren't there (shutting down), there's nothing to refresh.
        try:
            self.query_one("#status", Static).update(self._status_text())
            self.query_one("#working", Static).update(self._working_text())
        except (NoMatches, ScreenStackError):
            pass

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

    def on_text_area_changed(self, event) -> None:
        """As the user types a bare slash command, surface the matching commands
        in the (idle) activity line — '/m' shows '/model'. Tab completes them."""
        if self._busy:
            return  # #working shows live activity while a turn is running
        try:
            text = self.query_one("#prompt", PromptArea).text
        except Exception:  # noqa: BLE001 — widget not mounted yet
            return
        hint = ""
        if text.startswith("/") and " " not in text and "\n" not in text:
            commands = SLASH_COMMANDS + ["/skills"] + [f"/{n}" for n in sorted(self._skills)]
            matches = [c for c in commands if c.startswith(text.lower())]
            if matches:
                hint = "  " + "   ".join(matches) + "   ·  Tab to complete"
        self.query_one("#working", Static).update(hint)

    def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        text = event.text.strip()
        prompt = self.query_one("#prompt", PromptArea)
        prompt.clear()
        if not text:
            return
        if is_slash_command(text):
            self._handle_slash(text)  # meta commands stay out of history
            return
        prompt.cmd_history.add(text)
        # Show the user turn immediately, in order. If a turn is already
        # running, queue this one and drain it when the current turn finishes
        # instead of dropping it on the floor.
        self._mount(UserMessage(text))
        # Confirm any image attachments so the user SEES vision is active (the
        # actual attach happens at the API boundary in providers).
        from drydock import providers
        imgs = providers.detect_image_paths(text)
        if imgs:
            import os as _os
            names = ", ".join(_os.path.basename(p) for p in imgs)
            self._info(f"📎 attached {len(imgs)} image(s) for the model to see: {names}")
        if self._busy:
            self._queue.append(text)
            self._refresh_status()
            return
        self._begin(text)

    def _begin(self, text: str) -> None:
        """Start an agent turn for an already-displayed user prompt."""
        self._current_assistant = None
        self._cancel.clear()  # fresh turn — clear any prior STOP
        # Reset the pinned plan so the previous task's checklist doesn't linger
        # in the panel during an unrelated new request, and a prior *unfinished*
        # plan can't fire a stale continue-nudge (_plan_has_unfinished) on this
        # turn. If this turn emits its own todo, _render_todo repopulates it.
        self.config.pop("_todo", None)
        self.query_one("#todo", Static).update("")
        self._turn_tools = set()
        self._turn_error = False
        self.query_one("#suggest", Static).update("")  # hide the hint while working
        self._busy = True
        self._work_start = time.monotonic()
        self._last_progress = self._work_start
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
        elif cmd == "/compact":
            self._cmd_compact()
        elif cmd == "/context":
            self._cmd_context(arg)
        elif cmd == "/shell":
            self._cmd_shell()
        elif cmd == "/events":
            self._cmd_events()
        elif cmd == "/advisor":
            self._cmd_advisor(arg)
        elif cmd == "/ask":
            self._cmd_ask(arg)
        elif cmd == "/ask!":
            self._cmd_ask(arg, inject=True)
        elif cmd == "/graphrag":
            self._cmd_graphrag(arg)
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
                "  /compact         shrink old context to free up the window\n"
                "  /context         view/set the context-window budget (e.g. /context 65536)\n"
                "  /advisor         set up a 2nd 'advisor' model (Gemini etc.); /ask <q> = you, /ask! = feed to agent\n"
                "  /graphrag        ingest docs into a knowledge base the agent can use\n"
                "                   build <path> · add <path> · query <q> · status · clear\n"
                "  /skills          list skills · /skills new <name> <prompt> to create one\n"
                "  /loop            /loop <count> <prompt> — repeat a prompt (Esc stops)\n"
                "  /mcp             list connected MCP servers and their tools\n"
                "  /rmf             RMF automation — /rmf bootstrap, then /rmf-control etc.\n"
                "  /stig            /stig new <xccdf> → blank .ckl; summarize; /stig-assess\n"
                "  /clear           reset the conversation\n"
                "  /quit            exit\n"
                "Type a task and press Enter. ↑/↓ recall history · Esc stops · Ctrl+O expands tools."
            )
        elif cmd == "/mcp":
            self._cmd_mcp()
        elif cmd == "/rmf":
            self._cmd_rmf(arg)
        elif cmd == "/stig":
            self._cmd_stig(arg)
        elif cmd == "/loop":
            self._cmd_loop(arg)
        elif cmd == "/skills":
            self._cmd_skills(arg)
        elif cmd[1:] in self._skills:
            self._run_skill(self._skills[cmd[1:]], arg)
        else:
            self._mount(ErrorMessage(f"unknown command: {cmd} (try /help)"))

    def _cmd_stig(self, arg: str) -> None:
        """Summarize a STIG checklist (.ckl/.cklb): asset + status counts, or list
        rules of a given status. Assess it with /stig-assess (loop for the whole
        checklist)."""
        from drydock import stig

        parts = arg.split()
        if not parts:
            self._info("usage:\n"
                       "  /stig new <benchmark-xccdf.xml> [out.ckl]  — blank .ckl from a STIG\n"
                       "  /stig <path.ckl|.cklb> [status]            — summary / list by status\n"
                       "  /stig graph <path.ckl>                     — ingest into the RMF graph\n"
                       "  /stig poam <path.ckl> [out.csv]            — eMASS POA&M CSV (open findings)\n"
                       "Assess:  /loop <n> /stig-assess <path>")
            return
        import os as _os
        cwd = self.config.get("cwd") or "."
        _abs = lambda p: p if _os.path.isabs(p) else _os.path.join(cwd, p)  # noqa: E731
        # /stig new <xccdf> [out.ckl] — generate a BLANK .ckl from a STIG XCCDF benchmark
        if parts[0].lower() == "new" and len(parts) > 1:
            xp = _abs(parts[1])
            out = _abs(parts[2]) if len(parts) > 2 else \
                _os.path.splitext(_abs(parts[1]))[0].replace("-xccdf", "") + ".ckl"
            try:
                cl = stig.xccdf_to_checklist(xp)
                cl.save(out)
            except Exception as e:  # noqa: BLE001
                self._mount(ErrorMessage(f"could not parse the STIG XCCDF: {e}"))
                return
            self._info(
                f"✓ Generated {out} from the STIG benchmark — {len(cl.rules)} rules, "
                "all Not_Reviewed. Now pull in the app's evidence and assess:\n"
                f"  /graphrag build <app-docs>   ·   /loop {len(cl.rules)} /stig-assess {out}"
            )
            return
        # /stig poam <path> [out.csv] — export open findings to an eMASS POA&M CSV
        if parts[0].lower() == "poam" and len(parts) > 1:
            cp = _abs(parts[1])
            out = _abs(parts[2]) if len(parts) > 2 else \
                _os.path.splitext(cp)[0] + "_poam.csv"
            self._info("Building the eMASS POA&M CSV (mapping open findings to NIST "
                       "controls via the CCI map)…")
            self.run_worker(lambda: self._stig_poam(cwd, cp, out), thread=True)
            return
        # /stig graph <path> — ingest the checklist into the RMF typed graph
        if parts[0].lower() == "graph" and len(parts) > 1:
            gp = parts[1] if _os.path.isabs(parts[1]) else _os.path.join(cwd, parts[1])
            self._info("Ingesting the checklist into the RMF graph "
                       "(fetching the DISA CCI→800-53 map on first use)…")
            self.run_worker(lambda: self._stig_graph(cwd, gp), thread=True)
            return
        path = parts[0]
        if not _os.path.isabs(path):
            path = _os.path.join(cwd, path)
        try:
            cl = stig.load(path)
        except Exception as e:  # noqa: BLE001
            self._mount(ErrorMessage(f"could not read checklist: {e}"))
            return
        status = parts[1] if len(parts) > 1 else None
        self._info("\n".join(stig.summary_lines(cl, parts[0], status)))

    def _cmd_rmf(self, arg: str) -> None:
        """RMF automation: bootstrap the NIST 800-53 catalog into the knowledge
        base and surface the bundled RMF skills."""
        from drydock import rmf

        parts = arg.split()
        sub = parts[0].lower() if parts else ""
        cwd = self.config.get("cwd") or "."
        if sub == "bootstrap":
            families = [f.lower() for f in parts[1:]] or None
            scope = ("families " + ", ".join(families)) if families else "all 20 families"
            self._info(
                f"Ingesting the NIST SP 800-53 Rev 5 catalog ({scope}) into the "
                "knowledge base — one-time download + index, this can take ~30s…"
            )
            self.run_worker(lambda: self._rmf_bootstrap(cwd, families), thread=True)
        elif sub in ("", "help", "status"):
            cat = rmf.rmf_dir(cwd) / "catalog.json"
            have = "downloaded" if cat.exists() else "not downloaded yet"
            self._info(
                "RMF automation — automate Risk Management Framework work.\n"
                f"  Catalog (NIST 800-53 Rev 5): {have} ({cat})\n"
                "  /rmf bootstrap [families…]   ingest the control catalog (e.g. "
                "/rmf bootstrap ac si, or no args for all)\n"
                "Skills (run as /<name>):\n"
                "  /rmf-control <id>      look up a control (e.g. /rmf-control AC-2)\n"
                "  /rmf-categorize …      FIPS 199 categorization + tailored baseline\n"
                "  /rmf-review <control>  review an SSP implementation statement\n"
                "  /rmf-poam <finding>    generate a POA&M entry\n"
                "Ingest your own SSPs/POA&Ms (PDF/Word/text) with /graphrag build "
                "<path> so the skills can cross-reference them."
            )
        else:
            self._info("usage:  /rmf bootstrap [families…]  ·  /rmf status")

    def _rmf_bootstrap(self, cwd: str, families) -> None:
        """Worker-thread body: fetch + ingest the catalog, report back on the UI."""
        from drydock import rmf

        try:
            stats = rmf.bootstrap(cwd, families=families)
            gstats = stats.get("graph", {})
            msg = (
                f"✓ RMF catalog ingested: {stats['family_docs']} family doc(s), "
                f"{stats['chunks']} KB chunks + a typed graph ({gstats.get('nodes', 0)} "
                f"nodes, {gstats.get('edges', 0)} edges). Try /rmf-control AC-2 (text) "
                "or ask the agent to trace relationships (GraphQuery / GraphAdd)."
            )
        except Exception as e:  # noqa: BLE001
            msg = (f"RMF bootstrap failed: {e}. (Needs internet for the one-time "
                   "catalog download; after that it works offline.)")
        self.call_from_thread(self._info, msg)

    def _stig_poam(self, cwd: str, path: str, out: str) -> None:
        """Worker: export a checklist's open findings to an eMASS POA&M CSV,
        pulling each finding's NIST control from the CCI map. Deterministic."""
        from drydock import cci, poam, stig

        try:
            cl = stig.load(path)
            cci_map = cci.load_map(cwd)   # offline-safe ({} → Controls show '(unmapped)')
            r = poam.export(cl, cci_map, out)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._mount, ErrorMessage(f"could not build POA&M: {e}"))
            return
        note = "" if cci_map else " (CCI map unavailable offline — Control column shows '(unmapped)'; re-run online to populate it)"
        self.call_from_thread(
            self._info,
            f"✓ Wrote {r['rows']} POA&M row(s) for the open findings to {r['path']} "
            f"(eMASS headers: Control · Vulnerability Description · POA&M Status=Ongoing · "
            f"Milestone · Severity).{note}"
        )

    def _stig_graph(self, cwd: str, path: str) -> None:
        """Worker-thread body: ingest a checklist into the RMF graph, auto-linking
        rules to NIST controls via the DISA CCI map. Reports back on the UI."""
        from drydock import cci, rmf_graph, stig

        try:
            cl = stig.load(path)
            cci_map = cci.load_map(cwd)        # fetch+cache once; offline-safe ({} on failure)
            g = rmf_graph.RmfGraph.load(rmf_graph.graph_path(cwd))
            r = rmf_graph.ingest_checklist(g, cl, cci_map)
            g.save(rmf_graph.graph_path(cwd))
            link_note = (
                f"auto-linked {r['linked']}/{r['rules']} rules to NIST controls via CCI "
                "(Control —SATISFIED_BY→ rule). Trace with GraphQuery control <id>."
                if r["linked"] else
                "(no CCI→control links — the CCI map was unavailable offline; rules are "
                "still in the graph. Re-run online, or use GraphAdd satisfies.)"
            )
            msg = (f"✓ Ingested {r['rules']} STIG rules for host '{r['host']}' into the "
                   f"RMF graph (STIG/STIG-Rule + PART_OF/APPLIES_TO/EVALUATES); {link_note}")
        except Exception as e:  # noqa: BLE001
            msg = f"could not graph checklist: {e}"
        self.call_from_thread(self._info, msg)

    def _cmd_mcp(self) -> None:
        """List connected MCP servers and the tools they expose."""
        from drydock import mcp

        servers = mcp.connected()
        lines: list[str] = []
        if not servers:
            lines.append(
                "No MCP servers connected. Configure them in ~/.drydock/mcp.json "
                "(or <project>/.drydock/mcp.json) under \"mcpServers\", then restart."
            )
        else:
            lines.append(f"MCP servers ({len(servers)} connected):")
            for name, srv in servers.items():
                tools = ", ".join(t["name"] for t in srv.tools) or "(no tools)"
                lines.append(f"  • {name}: {tools}")
            lines.append("Call them as mcp__<server>__<tool> (the model does this automatically).")
        for msg in self.config.get("mcp_log") or []:
            lines.append(f"  · {msg}")
        self._info("\n".join(lines))

    def _cmd_loop(self, arg: str) -> None:
        """/loop <count> <prompt> — run <prompt> up to <count> times (1–50),
        re-submitting after each turn finishes. Esc (or /stop) ends the loop."""
        parts = arg.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            self._info(
                "usage: /loop <count> <prompt>   e.g.  /loop 5 fix the next "
                "failing test and run pytest\n(count 1–50; Esc stops the loop)"
            )
            return
        count = max(1, min(int(parts[0]), 50))
        prompt = parts[1].strip()
        if not prompt:
            self._info("usage: /loop <count> <prompt>")
            return
        if self._busy:
            self._info("A turn is already running — stop it (Esc) before starting a loop.")
            return
        self._repeat = {"prompt": prompt, "remaining": count, "total": count}
        self._info(f"↻ looping {count}× (Esc to stop):  {prompt}")
        self._mount(UserMessage(prompt))
        self._begin(prompt)

    def _cmd_skills(self, arg: str = "") -> None:
        """List skills, or create one: /skills new <name> <prompt…> (use $ARGS in
        the prompt for trailing input). Created skills are usable as /<name>
        immediately."""
        from drydock import skills as skillsmod

        parts = arg.split(maxsplit=2)
        if parts and parts[0].lower() == "new":
            if len(parts) < 3:
                self._info(
                    "usage: /skills new <name> <prompt text>\n"
                    "  e.g.  /skills new review  Run GitDiff, then review the changes "
                    "for bugs.\n  (use $ARGS in the prompt for trailing input)"
                )
                return
            name, body = parts[1], parts[2]
            try:
                path = skillsmod.create_skill(name, body, cwd=self.config.get("cwd") or ".")
            except ValueError as e:
                self._info(f"Couldn't create skill: {e}")
                return
            self._skills = skillsmod.load_skills(self.config.get("cwd") or ".")  # reload
            self._info(f"✓ Created skill /{name.lower()} ({path}). Invoke it as /{name.lower()}.")
            return
        if not self._skills:
            self._info(
                "No skills yet. Create one with:  /skills new <name> <prompt text>\n"
                "(or drop a markdown file in ~/.drydock/skills/<name>.md). Invoke as "
                "/<name>; use $ARGS for trailing input."
            )
            return
        lines = ["Skills (invoke as /<name>):"]
        for name in sorted(self._skills):
            sk = self._skills[name]
            lines.append(f"  /{name}" + (f"  — {sk.description}" if sk.description else ""))
        lines.append("Create one:  /skills new <name> <prompt text>")
        self._info("\n".join(lines))

    def _run_skill(self, skill, arg: str) -> None:
        """Expand a skill into a prompt and run it as a normal user turn."""
        prompt = skill.render(arg)
        self._mount(UserMessage(f"/{skill.name}" + (f" {arg}" if arg else "")))
        if self._busy:
            self._queue.append(prompt)
            self._refresh_status()
        else:
            self._begin(prompt)

    def _cmd_events(self) -> None:
        """Show a digest of this session's durable execution trace (event log)."""
        log = self.state.events
        if log is None:
            self._info("Event log is off (config event_log = false).")
            return
        from drydock.events import summarize
        s = summarize(str(log.path))
        tools = ", ".join(f"{k}×{v}" for k, v in sorted(s["tools"].items())) or "none"
        v = s["verifications"]
        lines = [
            f"Task trace — {log.path}",
            f"  objective    : {(s['objective'] or '(none yet)')[:80]}",
            f"  criteria     : {len(s['acceptance_criteria'])}",
            f"  phase        : {s['final_phase']}",
            f"  turns        : {s['turns']}   tools: {tools}",
            f"  verifications: {v['pass']} passed, {v['fail']} failed",
            f"  tokens       : {s['in_tok']:,} in / {s['out_tok']:,} out   ({s['event_count']} events)",
        ]
        self._info("\n".join(lines))

    def _cmd_shell(self) -> None:
        """Show exactly which shell the Bash tool runs commands through, plus the
        platform signals — so a 'still using bash on Windows' report is instantly
        diagnosable. (The tool is NAMED 'Bash' on every OS; on Windows it actually
        invokes PowerShell/cmd underneath.)"""
        import os as _os
        import sys as _sys

        from drydock import tools
        kind, path = tools._detect_shell()
        lines = [
            f"The Bash tool runs your commands through: {kind.upper()}",
            f"  shell path : {path}",
            f"  os.name={_os.name}  sys.platform={_sys.platform}  detected_windows={tools._IS_WINDOWS}",
            f"  DRYDOCK_SHELL env = {_os.environ.get('DRYDOCK_SHELL') or '(unset)'}",
            f"  bash on system    = {tools._detect_bash() or '(none)'}",
        ]
        if kind == "bash" and tools._IS_WINDOWS:
            lines.append("  ⚠ on Windows but using bash — set  $env:DRYDOCK_SHELL=\"powershell\"  and restart.")
        elif kind == "powershell":
            lines.append("  ✓ using PowerShell (the 'Bash' label is just the tool's name).")
        lines.append("  Force a shell any time: set env DRYDOCK_SHELL to powershell / cmd / bash.")
        self._info("\n".join(lines))

    def _cmd_context(self, arg: str) -> None:
        """View or change the context-window budget (tokens) — the cap that drives
        the ctx gauge + auto-compaction. `/context` shows it + its source; `/context
        <n>` sets and PERSISTS it to ~/.drydock/config.toml. This is the lever for
        'stuck at 32k': an old config.toml value (drydock never rewrites an existing
        one) or a smaller model-server -c silently caps you."""
        limit = self.config.get("context_limit", 65536) or 65536
        arg = (arg or "").strip()
        if not arg:
            self._info(
                f"context_limit (drydock): {limit:,} tokens (the '/{limit // 1024}k' in the ctx gauge).\n"
                "Source order: built-in default 65536 < ~/.drydock/config.toml < --context-limit.\n"
                "Probing your model server for its REAL context window…\n"
                "  To change drydock's budget:  /context <tokens>   (saved to config.toml)"
            )
            self.run_worker(lambda: self._probe_server_context(limit), thread=True)
            return
        try:
            n = int(arg.replace(",", "").replace("k", "000").replace("K", "000"))
            if n < 4096 or n > 2_000_000:
                raise ValueError
        except ValueError:
            self._mount(ErrorMessage("usage: /context <tokens>  (4096–2000000, e.g. /context 65536)"))
            return
        self.config["context_limit"] = n
        self._persist_config()
        self._refresh_status()  # repaint the gauge against the new budget
        self._info(
            f"✓ context_limit set to {n:,} tokens and saved to ~/.drydock/config.toml.\n"
            "Make sure your model server's context (-c / --ctx-size / max_model_len) is at\n"
            "least this, or the server will still cap you below it."
        )

    def _probe_server_context(self, limit: int) -> None:
        """Worker: ask the model server its real context window and report whether
        IT (not drydock's config) is the thing capping you — the definitive answer
        to 'stuck at N tokens'."""
        from drydock import providers

        base_url = self.config.get("base_url") or providers.PROVIDERS.get(
            self.config.get("provider") or "vllm", {}).get("base_url", "http://localhost:8000/v1")
        n_ctx = providers.probe_server_context(base_url)
        if n_ctx is None:
            msg = (f"Model server ({base_url}) didn't report its context size "
                   "(not llama.cpp /props or vLLM max_model_len, or unreachable). "
                   f"Your effective cap is the smaller of drydock's {limit:,} and the "
                   "server's own -c/--ctx-size.")
        elif n_ctx < limit:
            # Auto-adopt the server's real per-request window so proactive
            # compaction + the gauge target it (prevents the context-overflow 400).
            self.config["context_limit"] = n_ctx
            self.call_from_thread(self._refresh_status)
            msg = (f"⚠ Model server allows n_ctx = {n_ctx:,} tokens PER REQUEST — SMALLER "
                   f"than drydock's {limit:,}, so it's your real cap. drydock is now using "
                   f"{n_ctx:,} (compaction targets it). If the server's TOTAL context is "
                   "bigger, this is usually PARALLEL SLOTS dividing it (llama.cpp splits -c "
                   "across -np, e.g. 262144/8 = 32768 each) — restart with -np 1 for the "
                   f"full window, or set context_limit = {n_ctx} in config.toml to keep this.")
        else:
            msg = (f"✓ Model server reports n_ctx = {n_ctx:,} tokens (≥ drydock's {limit:,}), "
                   "so drydock's budget is the effective limit — no server-side cap.")
        self.call_from_thread(self._info, msg)

    def _cmd_advisor(self, arg: str) -> None:
        """View/set the optional second 'advisor' model (a stronger model, e.g.
        Gemini, on any OpenAI-compatible endpoint). Persists to config.toml."""
        arg = (arg or "").strip()
        if not arg:
            from drydock import advisor
            m = self.config.get("advisor_model") or "(unset)"
            u = self.config.get("advisor_base_url") or "(unset)"
            has_key = "yes" if (self.config.get("advisor_api_key") or "").strip() else "no"
            state = "configured ✓" if advisor.is_configured(self.config) else "not configured"
            self._info(
                f"advisor ({state}):\n"
                f"  model:    {m}\n"
                f"  endpoint: {u}\n"
                f"  api key:  {has_key}\n"
                "Set up:  /advisor url <base_url/v1>  ·  /advisor model <name>  ·  "
                "/advisor key <api_key>  ·  /advisor test\n"
                "Then:  /ask <question>  (you)  or the agent calls the Consult tool."
            )
            return
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower()
        val = parts[1].strip() if len(parts) > 1 else ""
        if sub == "test":
            from drydock import advisor
            if not advisor.is_configured(self.config):
                self._info(advisor.not_configured_message())
                return
            self._info(f"Testing the advisor endpoint ({self.config.get('advisor_base_url')})…")
            self.run_worker(lambda: self.call_from_thread(
                self._info, advisor.test_connection(self.config)), thread=True)
            return
        keymap = {"url": "advisor_base_url", "endpoint": "advisor_base_url",
                  "model": "advisor_model", "key": "advisor_api_key", "api_key": "advisor_api_key"}
        if sub not in keymap or not val:
            self._mount(ErrorMessage(
                "usage: /advisor [url <base_url> | model <name> | key <api_key> | test]"))
            return
        self.config[keymap[sub]] = val
        self._persist_config()
        shown = "•••••" if keymap[sub] == "advisor_api_key" else val
        self._info(f"✓ advisor {sub} set to {shown} (saved to config.toml).")

    def _cmd_ask(self, arg: str, *, inject: bool = False) -> None:
        """Consult the advisor model. `/ask` shows the answer to YOU only;
        `/ask!` also INJECTS it into the agent's context and has the primary model
        process it (so a second opinion can steer the current task)."""
        from drydock import advisor
        q = (arg or "").strip()
        if not q:
            self._info("usage: /ask <question>  (show to you)  ·  /ask! <question>  "
                       "(also feed the answer to the agent)")
            return
        if not advisor.is_configured(self.config):
            self._info(advisor.not_configured_message())
            return
        self._info(f"Asking the advisor ({self.config.get('advisor_model')})"
                   + (" — its answer will be added to the agent's context…" if inject else "…"))
        self.run_worker(lambda: self._ask_worker(q, inject), thread=True)

    def _ask_worker(self, question: str, inject: bool = False) -> None:
        from drydock import advisor
        answer = advisor.consult(question, self.config)
        self.call_from_thread(self._deliver_advice, question, answer, inject)

    def _deliver_advice(self, question: str, answer: str, inject: bool) -> None:
        """Show the advisor's answer; if inject, feed it into the agent's context
        so the primary model processes it (queued if a turn is already running)."""
        failed = answer.startswith(("No advisor", "Could not reach", "Error", "✗"))
        if not inject or failed:
            self._info(f"💡 advisor:\n{answer}")
            return
        self._info(f"💡 advisor (added to the agent's context):\n{answer}")
        turn = (f"[Second opinion from the advisor model — I asked it: {question}]\n\n"
                f"{answer}\n\n[Consider this advice for the current task.]")
        if self._busy:
            self._queue.append(turn)
            self._refresh_status()
        else:
            self._begin(turn)

    def _cmd_compact(self) -> None:
        """Manually compact the conversation to reclaim context NOW, without
        waiting for the automatic 60%-of-window threshold (agent.maybe_compact).
        Truncates/drops old tool results and oversized tool-call arguments while
        keeping recent turns intact (see compaction.compact). If a normal pass
        can't free enough (history dominated by big messages, not tool output),
        it ESCALATES to emergency_compact so an explicit /compact always helps —
        the user asked to free space, so being aggressive is the right call."""
        from drydock.compaction import compact, emergency_compact, estimate_tokens

        msgs = self.state.messages
        if not msgs:
            self._info("Nothing to compact — the conversation is empty.")
            return
        before = estimate_tokens(msgs)
        limit = self.config.get("context_limit", 65536) or 65536
        self.state.messages = compact(msgs, limit)
        # Escalate when the normal pass left us still heavy (>50% of the window):
        # this is exactly the "tried /compact, it said nothing, then OOM again"
        # case — the bloat isn't in droppable tool results, so go aggressive.
        if estimate_tokens(self.state.messages) > limit * 0.5:
            self.state.messages = emergency_compact(self.state.messages, limit)
        after = estimate_tokens(self.state.messages)
        saved = before - after
        if saved > 0:
            # Only touch the gauge when we ACTUALLY shrank the history. The gauge
            # normally shows the server's exact prompt-token count; our estimate
            # (chars/3) runs lower, so overwriting it on a no-op compaction would
            # fake a large drop. Scale the real count by the same ratio we shrank
            # the estimate, so the gauge moves proportionally and stays honest;
            # the next real turn replaces it with the server's exact count.
            self._ctx_tokens = round(self._ctx_tokens * (after / before)) if before else after
            self._refresh_status()
            self._info(
                f"Compacted context: ~{before:,} → ~{after:,} tokens "
                f"(freed ~{saved:,}). Older tool output was truncated/dropped; "
                "recent turns are kept intact."
            )
        else:
            self._info(
                f"Already compact — ~{before:,} tokens, nothing to free "
                "(recent turns are always preserved)."
            )

    def _graphrag_build(self, sub: str, rest: str, store, cwd: str) -> None:
        """Worker: build/add a knowledge base off the UI thread, streaming a
        throttled progress line so a huge folder never looks frozen."""
        from drydock import graphrag
        import time as _t

        last = [0.0]

        def progress(files_done: int, src: str) -> None:
            now = _t.monotonic()
            if now - last[0] >= 1.0:          # throttle UI updates to ~1/s
                last[0] = now
                self.call_from_thread(
                    self.query_one("#working", Static).update,
                    f"  ⚓ indexing… {files_done} files ({src[-48:]})")

        try:
            fn = graphrag.build_index if sub == "build" else graphrag.add_to_index
            stats = fn([rest], store, cwd=cwd, progress=progress)
        except Exception as e:  # noqa: BLE001 — surface, never crash the TUI
            self.call_from_thread(self.query_one("#working", Static).update, "")
            self.call_from_thread(self._mount, ErrorMessage(f"graphrag {sub} failed: {e}"))
            return
        self.call_from_thread(self.query_one("#working", Static).update, "")
        if sub == "add" and stats["files"] == 0:
            self.call_from_thread(
                self._info, f"No new documents under {rest} (already indexed, or no text found).")
            return
        if not stats["chunks"]:
            self.call_from_thread(self._info, f"No text found under {rest}. Nothing was indexed.")
            return
        verb2 = "built" if sub == "build" else f"updated (+{stats['files']} new files)"
        self.call_from_thread(
            self._info,
            f"✓ Knowledge base {verb2}: {stats['chunks']} chunks · "
            f"{stats['entities']} entities · {stats['edges']} edges.\n"
            f"Stored at {store}. The agent draws on it via the Knowledge tool.")

    def _cmd_graphrag(self, arg: str) -> None:
        """Build / inspect / clear the project's GraphRAG knowledge base. Once
        built, the agent retrieves from it via the read-only Knowledge tool."""
        from drydock import graphrag

        cwd = self.config.get("cwd") or "."
        store = graphrag.default_store_path(cwd)
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("build", "add"):
            if not rest:
                self._info(f"usage: /graphrag {sub} <path>   (a file or directory of docs/code)")
                return
            verb = "Rebuilding" if sub == "build" else "Ingesting into"
            self._info(f"{verb} knowledge base from {rest} … (runs in the background; "
                       "progress below — the TUI stays responsive)")
            # Run OFF the UI thread so a large folder can't freeze the interface,
            # and stream progress (files indexed) back so it never looks locked up.
            self.run_worker(lambda: self._graphrag_build(sub, rest, store, cwd), thread=True)
        elif sub == "query":
            if not rest:
                self._info("usage: /graphrag query <question>   (test what the KB returns)")
                return
            index = graphrag.load_index(store)
            if index is None:
                self._info("No knowledge base yet. Build one:  /graphrag build <path>")
                return
            res = graphrag.query_index(index, rest, k=3)
            self._info(
                graphrag.format_results(res, rest)
                + "\n\n— This is a PREVIEW for you; it does not go to the model. "
                "The agent retrieves from this knowledge base automatically (via "
                "its Knowledge tool) when you just ASK a question — no slash command."
            )
        elif sub in ("", "status"):
            index = graphrag.load_index(store)
            if index is None:
                self._info("No knowledge base yet. Build one:  /graphrag build <path>")
            else:
                srcs = graphrag.sources(index)
                st = graphrag.index_stats(index)
                shown = "\n".join(f"    · {s}" for s in srcs[:20])
                more = f"\n    … +{len(srcs) - 20} more" if len(srcs) > 20 else ""
                resolved = graphrag._resolve_store(store)
                legacy = "  ⚠ legacy JSON — run /graphrag migrate for fast queries" \
                    if str(resolved).endswith(".json") else ""
                self._info(
                    f"Knowledge base: {st['chunks']} chunks · {st['entities']} entities · "
                    f"{len(srcs)} sources ({resolved}).{legacy}\n{shown}{more}"
                )
        elif sub == "migrate":
            resolved = graphrag._resolve_store(store)
            if not str(resolved).endswith(".json"):
                self._info("Already using the fast SQLite store — nothing to migrate.")
                return
            db = resolved.with_suffix(".db")
            self._info(f"Migrating legacy index → {db} (one-time; loads the JSON once) …")
            try:
                m = graphrag.migrate_json_to_sqlite(resolved, db)
            except Exception as e:  # noqa: BLE001
                self._mount(ErrorMessage(f"migrate failed: {e}"))
                return
            self._info(
                f"✓ Migrated: {m['chunks']} chunks · {m['entities']} entities → {db}. "
                f"Queries now hit the index directly (no full-file load). You can delete "
                f"the old {resolved.name} once you've confirmed queries work."
            )
        elif sub == "clear":
            try:
                graphrag._resolve_store(store).unlink(missing_ok=True)
                self._info("Knowledge base cleared.")
            except OSError as e:
                self._mount(ErrorMessage(f"could not clear: {e}"))
        else:
            self._info(
                "usage:  /graphrag build <path>  ·  add <path>  ·  query <question>  "
                "·  status  ·  migrate  ·  clear"
            )

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
          /model url <base_url>       set the server URL (e.g. http://localhost:8000/v1)
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
                "Set up:  /model <name>  ·  /model url <http://localhost:8000/v1>  ·  "
                "/model provider <vllm|ollama|lmstudio|openai>"
            )
            return
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower()
        val = parts[1].strip() if len(parts) > 1 else ""
        if sub == "url":
            if not val:
                self._info("usage: /model url <http://localhost:8000/v1>")
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
                # Any event = the model/server is making progress → reset the
                # stall watchdog. During a real stall the provider stream blocks
                # and NO event arrives, so this timestamp freezes and
                # _working_text surfaces a hint.
                self._last_progress = time.monotonic()
                if isinstance(ev, ReasoningChunk):
                    self.post_message(AgentReasoning(ev.text))
                elif isinstance(ev, TextChunk):
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

    def on_agent_reasoning(self, m: AgentReasoning) -> None:
        # The model's thinking for this turn, rendered as a collapsed card BEFORE
        # the answer text (reasoning is yielded before TextChunk). End any current
        # text block so the answer starts fresh below the card.
        self._current_assistant = None
        self._mount(ReasoningCard(m.text))
        self._scroll.scroll_end(animate=False)

    def on_agent_text(self, m: AgentText) -> None:
        self._ensure_assistant().append(m.text)
        self._work_chars += len(m.text)
        self._scroll.scroll_end(animate=False)

    def on_agent_tool_start(self, m: AgentToolStart) -> None:
        self._current_assistant = None  # end the current text block
        self._turn_tools.add(m.name)
        if m.name == "todo":
            self._render_todo(m.inputs.get("tasks", ""))
            self._last_card = None  # tool_end is a no-op for the checklist
            return
        from drydock.tools import tool_display_name
        card = ToolCard(tool_display_name(m.name), summarize_inputs(m.inputs))
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
        self._plan_remaining = any(s != "done" for _, s in items)
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
        # /loop: re-run the prompt until the iteration count is exhausted (Esc/
        # stop clears self._repeat). Queued user prompts take priority (above).
        if self._repeat and self._repeat["remaining"] > 1 and not self._cancel.is_set():
            self._repeat["remaining"] -= 1
            done = self._repeat["total"] - self._repeat["remaining"] + 1
            self._info(f"↻ loop iteration {done}/{self._repeat['total']}")
            self._begin(self._repeat["prompt"])
            return
        self._repeat = None
        self._refresh_status()
        self._update_suggestion()
        self.query_one("#prompt", PromptArea).focus()

    def _update_suggestion(self) -> None:
        """Compute + render the dimmed recommended-next-command hint (Claude-Code-
        style). Empty when nothing useful to suggest."""
        from drydock.suggest import suggest_next_command
        limit = self.config.get("context_limit", 65536) or 65536
        pct = min(100, round(self._ctx_tokens / limit * 100)) if limit else 0
        wrote = bool(self._turn_tools & {"Write", "Edit"})
        ran = "Bash" in self._turn_tools
        self._suggestion = suggest_next_command(
            ctx_pct=pct, wrote_files=wrote, ran_bash=ran, had_error=self._turn_error,
            in_git=self._in_git, plan_remaining=self._plan_remaining,
        ) or ""
        hint = self.query_one("#suggest", Static)
        if self._suggestion:
            from rich.markup import escape
            hint.update(f"→ next: {escape(self._suggestion)}   [dim](ctrl+n to use)[/]")
        else:
            hint.update("")

    def action_use_suggestion(self) -> None:
        """Accept the recommended next command — drop it into the prompt to edit/send."""
        if self._suggestion and not self._busy:
            prompt = self.query_one("#prompt", PromptArea)
            prompt.text = self._suggestion
            prompt.move_cursor(prompt.document.end)
            prompt.focus()

    def on_agent_error(self, m: AgentError) -> None:
        self._turn_error = True
        self._mount(ErrorMessage(m.error))


def run_tui(config: dict) -> None:
    # Mouse ON (default): the wheel scrolls the transcript and the scrollbar
    # works. Text selection is in-app — drag to select (Textual highlights it),
    # then Ctrl+C copies (no Shift needed). Shift+drag still does a terminal-
    # native selection as a fallback. You can't have BOTH wheel-scroll and
    # terminal-native plain-drag selection — the mouse can only go to one — and
    # wheel scroll is the more-missed affordance, so the app keeps the mouse.
    DrydockApp(config).run()
