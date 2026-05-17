from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from enum import StrEnum, auto
import hashlib
from http import HTTPStatus
import json
import logging
import os
from pathlib import Path
from threading import Thread
import time
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel

from drydock.cli.terminal_setup import detect_terminal
from drydock.core.agents.manager import AgentManager
from drydock.core.agents.models import AgentProfile, BuiltinAgentName
from drydock.core.config import Backend, ProviderConfig, DrydockConfig
from drydock.core.llm.backend.factory import BACKEND_FACTORY
from drydock.core.llm.exceptions import BackendError
from drydock.core.llm.format import (
    APIToolFormatHandler,
    FailedToolCall,
    ResolvedMessage,
    ResolvedToolCall,
)
from drydock.core.llm.types import BackendLike
from drydock.core.middleware import (
    CHAT_AGENT_EXIT,
    CHAT_AGENT_REMINDER,
    PLAN_AGENT_EXIT,
    AutoCompactMiddleware,
    ContextWarningMiddleware,
    ConversationContext,
    MiddlewareAction,
    MiddlewarePipeline,
    MiddlewareResult,
    PriceLimitMiddleware,
    ReadOnlyAgentMiddleware,
    ResetReason,
    TurnLimitMiddleware,
    make_plan_agent_reminder,
)
from drydock.core.plan_session import PlanSession
from drydock.core.prompts import UtilityPrompt
from drydock.core.session.session_logger import SessionLogger
from drydock.core.session.session_migration import migrate_sessions_entrypoint
from drydock.core.skills.manager import SkillManager
from drydock.core.system_prompt import get_universal_system_prompt
from drydock.core.telemetry.send import TelemetryClient
from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    InvokeContext,
    ToolError,
    ToolPermission,
    ToolPermissionError,
)
from drydock.core.tools.manager import ToolManager
from drydock.core.tools.mcp import MCPRegistry
from drydock.core.tools.mcp_sampling import MCPSamplingHandler
from drydock.core.trusted_folders import has_agents_md_file
from drydock.core.types import (
    AgentStats,
    ApprovalCallback,
    ApprovalResponse,
    AssistantEvent,
    AsyncApprovalCallback,
    BaseEvent,
    CompactEndEvent,
    CompactStartEvent,
    EntrypointMetadata,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    MessageList,
    RateLimitError,
    ReasoningEvent,
    Role,
    SyncApprovalCallback,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserInputCallback,
    UserMessageEvent,
)
from drydock.core.utils import (
    TOOL_ERROR_TAG,
    DRYDOCK_STOP_EVENT_TAG,
    CancellationReason,
    get_user_agent,
    get_user_cancellation_message,
    is_user_cancellation_event,
)

try:
    from drydock.core.teleport.teleport import TeleportService as _TeleportService

    _TELEPORT_AVAILABLE = True
except ImportError:
    _TELEPORT_AVAILABLE = False
    _TeleportService = None

if TYPE_CHECKING:
    from drydock.core.teleport.nuage import TeleportSession
    from drydock.core.teleport.teleport import TeleportService
    from drydock.core.teleport.types import TeleportPushResponseEvent, TeleportYieldEvent


class ToolExecutionResponse(StrEnum):
    SKIP = auto()
    EXECUTE = auto()


class ToolDecision(BaseModel):
    verdict: ToolExecutionResponse
    approval_type: ToolPermission
    feedback: str | None = None


MAX_TOOL_TURNS = 200  # Bug fixes rarely need more than 50 turns; 200 is generous ceiling
MAX_API_ERRORS = 5


def _admiral_env_int(name: str, default: int) -> int:
    """Read a DRYDOCK_ADMIRAL_<name> env var at module-load; fall back
    to the hardcoded default on missing / empty / unparseable. This is
    the knob the meta-harness kernel writes when running a variant
    from research/experimenter.py. Production installs never set
    these vars, so behavior is unchanged for normal drydock users."""
    import os as _os
    v = _os.environ.get(f"DRYDOCK_ADMIRAL_{name}", "")
    if not v:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# Same exact call N+ times before warning. Env: DRYDOCK_ADMIRAL_REPEAT_WARNING_THRESHOLD
REPEAT_WARNING_THRESHOLD = _admiral_env_int("REPEAT_WARNING_THRESHOLD", 4)
# Same exact call N+ times before force-stop. Env: DRYDOCK_ADMIRAL_REPEAT_FORCE_STOP_THRESHOLD
REPEAT_FORCE_STOP_THRESHOLD = _admiral_env_int(
    "REPEAT_FORCE_STOP_THRESHOLD", 8)
# Check 0 (empty-result) threshold. Env: DRYDOCK_ADMIRAL_EMPTY_RESULT_THRESHOLD
EMPTY_RESULT_THRESHOLD = _admiral_env_int("EMPTY_RESULT_THRESHOLD", 3)
# Per-tool consecutive-call limits. Env: DRYDOCK_ADMIRAL_SAME_TOOL_NAME_REPEAT_LIMIT_{BASH,READ}
SAME_TOOL_NAME_REPEAT_LIMIT_BASH = _admiral_env_int(
    "SAME_TOOL_NAME_REPEAT_LIMIT_BASH", 5)
SAME_TOOL_NAME_REPEAT_LIMIT_READ = _admiral_env_int(
    "SAME_TOOL_NAME_REPEAT_LIMIT_READ", 7)

logger = logging.getLogger(__name__)


class AgentLoopError(Exception):
    """Base exception for AgentLoop errors."""


class AgentLoopStateError(AgentLoopError):
    """Raised when agent loop is in an invalid state."""


class AgentLoopLLMResponseError(AgentLoopError):
    """Raised when LLM response is malformed or missing expected data."""


class TeleportError(AgentLoopError):
    """Raised when teleport to Vibe Nuage fails."""


def _should_raise_rate_limit_error(e: Exception) -> bool:
    return isinstance(e, BackendError) and e.status == HTTPStatus.TOO_MANY_REQUESTS


class AgentLoop:
    def __init__(
        self,
        config: DrydockConfig,
        agent_name: str = BuiltinAgentName.DEFAULT,
        message_observer: Callable[[LLMMessage], None] | None = None,
        max_turns: int | None = None,
        max_price: float | None = None,
        backend: BackendLike | None = None,
        enable_streaming: bool = False,
        entrypoint_metadata: EntrypointMetadata | None = None,
    ) -> None:
        self._base_config = config
        self._max_turns = max_turns
        self._max_price = max_price
        self._plan_session = PlanSession()

        self.agent_manager = AgentManager(
            lambda: self._base_config, initial_agent=agent_name
        )
        self._mcp_registry = MCPRegistry()
        self.tool_manager = ToolManager(
            lambda: self.config, mcp_registry=self._mcp_registry
        )
        self.skill_manager = SkillManager(lambda: self.config)
        self.format_handler = APIToolFormatHandler()

        self.backend_factory = lambda: backend or self._select_backend()
        self.backend = self.backend_factory()
        self._sampling_handler = MCPSamplingHandler(
            backend_getter=lambda: self.backend, config_getter=lambda: self.config
        )

        self.message_observer = message_observer
        self.enable_streaming = enable_streaming
        self.middleware_pipeline = MiddlewarePipeline()
        self._setup_middleware()

        # Circuit breaker: track tool call signatures to prevent exact repeats
        # Key: hash(tool_name + args), Value: (count, last_result_snippet)
        self._tool_call_history: dict[str, tuple[int, str]] = {}
        self._consecutive_circuit_breaker_fires: int = 0
        self._empty_responses: int = 0
        self._successful_test_runs: int = 0

        # Mid-turn user injections (the Claude Code "type while busy" feature).
        # The TUI calls `queue_user_injection()` whenever the user submits a
        # message while the agent is mid-task. The per-turn loop drains this
        # at the top of each iteration and folds the text into context as a
        # SYSTEM note attached to the last tool result (the only ordering that
        # vLLM/Mistral accept after a tool turn). No locking needed — Textual's
        # event loop is single-threaded asyncio, same loop the agent runs on.
        self._pending_user_injections: list[str] = []

        # Shared read-file state — used by write_file / search_replace to
        # enforce Read-before-Write (per Claude Code's tool contract) and
        # by read_file to dedup unchanged-mtime re-reads. Keyed by resolved
        # absolute path; value is {"content", "timestamp", "offset", "limit"}.
        self._read_file_state: dict[str, dict] = {}

        system_prompt = get_universal_system_prompt(
            self.tool_manager, self.config, self.skill_manager, self.agent_manager
        )
        system_message = LLMMessage(role=Role.system, content=system_prompt)
        self.messages = MessageList(initial=[system_message], observer=message_observer)

        self.stats = AgentStats()
        try:
            active_model = config.get_active_model()
            self.stats.input_price_per_million = active_model.input_price
            self.stats.output_price_per_million = active_model.output_price
        except ValueError:
            pass

        self.approval_callback: ApprovalCallback | None = None
        self.user_input_callback: UserInputCallback | None = None

        self.entrypoint_metadata = entrypoint_metadata
        self.session_id = str(uuid4())
        self._current_user_message_id: str | None = None

        self.telemetry_client = TelemetryClient(config_getter=lambda: self.config)
        self.session_logger = SessionLogger(config.session_logging, self.session_id)
        self._teleport_service: TeleportService | None = None

        # Checkpoint store — lazily initialised on first user turn so we
        # can capture cwd at that point. None when disabled (e.g. tests).
        self._checkpoint_store: Any | None = None
        self._checkpoints_disabled: bool = False

        thread = Thread(
            target=migrate_sessions_entrypoint,
            args=(config.session_logging,),
            daemon=True,
            name="migrate_sessions",
        )
        thread.start()

        # Admiral Phase 3a: apply any `(model, unknown)` tuning knobs
        # before the loop starts. Safe no-op if no tuning is configured.
        self._admiral_task_type = "unknown"
        try:
            from drydock.admiral import tuning as _admiral_tuning
            _admiral_tuning.apply_to_agent_loop(self)
        except Exception as _e:  # never let Admiral break boot
            logger.debug("Admiral tuning apply failed: %s", _e)

        # Admiral Phase 3a: record session metrics on interpreter exit.
        # Use the live `session_id` (set above and matching the on-disk
        # session dir) — NOT a fresh uuid. Findings recorded against a
        # phantom uuid never resolved to a session log, so M5's offline
        # Deep Noir loop couldn't extract pairs from them.
        try:
            import atexit
            from drydock.admiral import metrics as _admiral_metrics
            atexit.register(
                lambda al=self: _admiral_metrics.record(
                    _admiral_metrics.collect(al, al.session_id, outcome="unknown")
                )
            )
        except Exception as _e:
            logger.debug("Admiral metrics hook failed: %s", _e)

    @property
    def agent_profile(self) -> AgentProfile:
        return self.agent_manager.active_profile

    @property
    def config(self) -> DrydockConfig:
        return self.agent_manager.config

    @property
    def auto_approve(self) -> bool:
        return self.config.auto_approve

    def queue_user_injection(self, text: str) -> None:
        """Queue a user message to be folded into context at the next turn boundary.

        Called by the TUI when the user submits a message while the agent is
        already running. The injection is drained at the top of the next
        per-turn iteration in `act()` and surfaced to the model as a SYSTEM
        note on the last tool result so message ordering stays valid for
        vLLM/Mistral.

        Side-effect: logs the queue event via the session logger so a
        replay (or a harness/watcher) can see that the message was
        accepted, even though it won't appear in `self.messages` until
        the drain runs. Without this, debugging "did my queued message
        land?" requires waiting for the next turn boundary.
        """
        cleaned = (text or "").strip()
        if cleaned:
            self._pending_user_injections.append(cleaned)
            try:
                self.session_logger.log_event({
                    "event": "user_injection_queued",
                    "text": cleaned[:1000],
                    "pending_count": len(self._pending_user_injections),
                })
            except Exception as _e:  # noqa: BLE001 — never block queueing on logger failure
                logger.debug("[injection] session log_event failed: %s", _e)

    def _drain_user_injections(self) -> None:
        """Pull any queued user messages into the current turn's context.

        Folds them onto the last tool result via the same safe path as
        `_inject_system_note` — never appends a fresh user-after-tool
        message, which vLLM/Mistral reject.
        """
        if not self._pending_user_injections:
            return
        # Snapshot + clear so a concurrent queue append doesn't double-fire.
        injections = self._pending_user_injections
        self._pending_user_injections = []
        for text in injections:
            note = (
                f"USER (typed while you were working — fold this into "
                f"the current task; do not start over):\n{text}"
            )
            self._inject_system_note(note)

    def set_tool_permission(
        self, tool_name: str, permission: ToolPermission, save_permanently: bool = False
    ) -> None:
        if save_permanently:
            DrydockConfig.save_updates({
                "tools": {tool_name: {"permission": permission.value}}
            })

        if tool_name not in self.config.tools:
            self.config.tools[tool_name] = BaseToolConfig()

        self.config.tools[tool_name].permission = permission
        self.tool_manager.invalidate_tool(tool_name)

    def emit_new_session_telemetry(self) -> None:
        entrypoint = (
            self.entrypoint_metadata.agent_entrypoint
            if self.entrypoint_metadata
            else "unknown"
        )
        has_agents_md = has_agents_md_file(Path.cwd())
        nb_skills = len(self.skill_manager.available_skills)
        nb_mcp_servers = len(self.config.mcp_servers)
        nb_models = len(self.config.models)

        terminal_emulator = None
        if entrypoint == "cli":
            terminal_emulator = detect_terminal().value

        self.telemetry_client.send_new_session(
            has_agents_md=has_agents_md,
            nb_skills=nb_skills,
            nb_mcp_servers=nb_mcp_servers,
            nb_models=nb_models,
            entrypoint=entrypoint,
            terminal_emulator=terminal_emulator,
        )

    def _select_backend(self) -> BackendLike:
        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)
        timeout = self.config.api_timeout
        return BACKEND_FACTORY[provider.backend](provider=provider, timeout=timeout)

    async def _save_messages(self) -> None:
        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self._base_config,
            self.tool_manager,
            self.agent_profile,
        )

    async def act(self, msg: str) -> AsyncGenerator[BaseEvent]:
        self._clean_message_history()

        # New user turn — reset per-turn counters so a previous turn can
        # never poison the current one.
        self._consecutive_circuit_breaker_fires = 0
        # Reset bash-test counter so "STOP testing" nudge only fires within
        # the current user prompt, not across the entire session. Without
        # this, by the 2nd prompt in a long session every bash call gets
        # the "project is WORKING, stop" note injected, causing model stalls
        # (empty_after_tool:bash fires in admiral).
        self._successful_test_runs = 0

        # Auto-create AGENTS.md if no project instructions exist.
        # devstral needs per-project AGENTS.md to anchor its behavior —
        # without it the model loops on ls/bash instead of using subagents.
        if self.stats.steps <= 1:
            self._ensure_agents_md()
            # Skip DRYDOCK.md auto-create under pytest so tmp_path-based
            # tests don't get unexpected files appearing in their fixture
            # dirs. Unit tests for the auto-create function call it
            # directly (bypassing this gate).
            if "PYTEST_CURRENT_TEST" not in os.environ:
                self._ensure_drydock_md()

        # Load project state for cross-session context
        try:
            from drydock.core.session.state_file import load_state
            state_content = load_state()
            if state_content:
                self._inject_system_note(
                    f"Previous session state:\n{state_content[:500]}"
                )
        except Exception:
            pass  # Non-critical

        async for event in self._conversation_loop(msg):
            yield event

        # Record a checkpoint at the END of the user turn — both
        # conversation pointer and code state are stable now. Best-effort:
        # checkpoint failures are non-fatal so they can never break the
        # main agent loop.
        try:
            self._record_checkpoint(label=msg[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[checkpoint] record skipped: %s", exc)

    # ------------------------------------------------------------------
    # Checkpoints — see drydock/core/checkpoint.py
    # ------------------------------------------------------------------

    # Agent-level state that should travel with each checkpoint so a
    # rewind also rolls back circuit-breaker counts, loop flags, etc.
    # Same set as the /clear and /compact resets — kept in sync there.
    _CHECKPOINT_STATE_FIELDS = (
        "_tool_call_history",
        "_consecutive_circuit_breaker_fires",
        "_empty_responses",
        "_successful_test_runs",
        "_loop_detected",
        "_loop_signal",
        "_hot_tool_path",
        "_consecutive_empty_turns",
        "_empty_nudge_last_user_idx",
        "_total_error_rounds",
        "_read_file_state",
    )

    def _capture_agent_state(self) -> dict:
        """Snapshot the counters/flags that should rewind with us.

        JSON-safe: tuples become lists, dicts pass through. Missing
        attributes default to None so older sessions don't crash on
        restore.
        """
        snap: dict = {}
        for name in self._CHECKPOINT_STATE_FIELDS:
            value = getattr(self, name, None)
            # Tuples need to round-trip through JSON; convert to list
            # and remember the type so restore can revert.
            if isinstance(value, tuple):
                snap[name] = {"_kind": "tuple", "items": list(value)}
            else:
                snap[name] = value
        return snap

    def _apply_agent_state(self, snap: dict) -> None:
        """Restore the counters/flags from a snapshot."""
        for name in self._CHECKPOINT_STATE_FIELDS:
            if name not in snap:
                continue
            value = snap[name]
            if isinstance(value, dict) and value.get("_kind") == "tuple":
                value = tuple(value.get("items", []))
            setattr(self, name, value)

    def _get_checkpoint_store(self):
        """Lazy-init the per-session CheckpointStore. Returns None on failure."""
        if self._checkpoint_store is not None:
            return self._checkpoint_store
        if self._checkpoints_disabled:
            return None
        try:
            from drydock.core.checkpoint import CheckpointStore
            self._checkpoint_store = CheckpointStore(
                work_tree=Path.cwd(), session_id=self.session_id,
            )
            return self._checkpoint_store
        except Exception as exc:  # noqa: BLE001
            logger.warning("[checkpoint] disabled: %s", exc)
            self._checkpoints_disabled = True
            return None

    def _record_checkpoint(self, label: str = "") -> None:
        store = self._get_checkpoint_store()
        if store is None:
            return
        store.record(
            msg_index=len(self.messages),
            label=label,
            agent_state=self._capture_agent_state(),
        )

    def restore_checkpoint(self, index: int, mode: str = "both") -> Any:
        """Restore to the checkpoint at `index` (0-based, oldest first).

        mode: "code" | "conversation" | "both".

        Returns the Checkpoint that was restored. Caller (TUI / CLI) is
        responsible for surfacing UI feedback.
        """
        store = self._get_checkpoint_store()
        if store is None:
            raise RuntimeError("checkpoints not available in this session")

        # Resolve negative indices the way Python lists do, so callers
        # can pass -1 for "the most recent one before HEAD".
        if index < 0:
            index = len(store.checkpoints) + index

        cp = store.restore(index, mode=mode)

        if mode in ("conversation", "both"):
            # Truncate the live message list back to where we were.
            keep = list(self.messages[: cp.msg_index])
            self.messages.reset(keep)
            # Roll back agent counters/flags to their state at that
            # point so circuit-breaker fires, loop flags, etc. don't
            # leak forward and re-poison the rewound session.
            self._apply_agent_state(cp.agent_state)

        return cp

    def list_checkpoints(self, limit: int | None = None) -> list:
        """Return checkpoints (most-recent first) for the picker UI."""
        store = self._get_checkpoint_store()
        if store is None:
            return []
        return store.list_checkpoints(limit=limit)

    @property
    def teleport_service(self) -> TeleportService:
        if not _TELEPORT_AVAILABLE:
            raise TeleportError(
                "Teleport requires git to be installed. "
                "Please install git and try again."
            )

        if self._teleport_service is None:
            if _TeleportService is None:
                raise TeleportError("_TeleportService is unexpectedly None")
            self._teleport_service = _TeleportService(
                session_logger=self.session_logger,
                nuage_base_url=self.config.nuage_base_url,
                nuage_workflow_id=self.config.nuage_workflow_id,
                nuage_api_key=self.config.nuage_api_key,
            )
        return self._teleport_service

    def teleport_to_nuage(
        self, prompt: str | None
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        from drydock.core.teleport.nuage import TeleportSession

        session = TeleportSession(
            metadata={
                "agent": self.agent_profile.name,
                "model": self.config.active_model,
                "stats": self.stats.model_dump(),
            },
            messages=[msg.model_dump(exclude_none=True) for msg in self.messages[1:]],
        )
        return self._teleport_generator(prompt, session)

    async def _teleport_generator(
        self, prompt: str | None, session: TeleportSession
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        from drydock.core.teleport.errors import ServiceTeleportError

        try:
            async with self.teleport_service:
                gen = self.teleport_service.execute(prompt=prompt, session=session)
                response: TeleportPushResponseEvent | None = None
                while True:
                    try:
                        event = await gen.asend(response)
                        response = yield event
                    except StopAsyncIteration:
                        break
        except ServiceTeleportError as e:
            raise TeleportError(str(e)) from e
        finally:
            self._teleport_service = None

    def _setup_middleware(self) -> None:
        """Configure middleware pipeline for this conversation."""
        self.middleware_pipeline.clear()

        if self._max_turns is not None:
            self.middleware_pipeline.add(TurnLimitMiddleware(self._max_turns))

        if self._max_price is not None:
            self.middleware_pipeline.add(PriceLimitMiddleware(self._max_price))

        active_model = self.config.get_active_model()
        _compact_thresh = active_model.auto_compact_threshold
        _env_thresh = os.environ.get("DRYDOCK_AUTO_COMPACT_THRESHOLD", "")
        if _env_thresh.strip():
            try:
                _compact_thresh = int(_env_thresh.strip())
            except ValueError:
                pass
        if _compact_thresh > 0:
            self.middleware_pipeline.add(
                AutoCompactMiddleware(_compact_thresh)
            )
            if self.config.context_warnings:
                self.middleware_pipeline.add(
                    ContextWarningMiddleware(0.5, _compact_thresh)
                )

        self.middleware_pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: self.agent_profile,
                BuiltinAgentName.PLAN,
                lambda: make_plan_agent_reminder(self._plan_session.plan_file_path_str),
                PLAN_AGENT_EXIT,
            )
        )
        self.middleware_pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: self.agent_profile,
                BuiltinAgentName.CHAT,
                CHAT_AGENT_REMINDER,
                CHAT_AGENT_EXIT,
            )
        )

    async def _handle_middleware_result(
        self, result: MiddlewareResult
    ) -> AsyncGenerator[BaseEvent]:
        match result.action:
            case MiddlewareAction.STOP:
                yield AssistantEvent(
                    content=f"<{DRYDOCK_STOP_EVENT_TAG}>{result.reason}</{DRYDOCK_STOP_EVENT_TAG}>",
                    stopped_by_middleware=True,
                )

            case MiddlewareAction.INJECT_MESSAGE:
                if result.message:
                    # Use safe injection to avoid user-after-tool role violations
                    self._inject_system_note(result.message)

            case MiddlewareAction.COMPACT:
                old_tokens = result.metadata.get(
                    "old_tokens", self.stats.context_tokens
                )
                threshold = result.metadata.get(
                    "threshold", self.config.get_active_model().auto_compact_threshold
                )
                tool_call_id = str(uuid4())

                yield CompactStartEvent(
                    tool_call_id=tool_call_id,
                    current_context_tokens=old_tokens,
                    threshold=threshold,
                )
                self.telemetry_client.send_auto_compact_triggered()

                summary = await self.compact()

                yield CompactEndEvent(
                    tool_call_id=tool_call_id,
                    old_context_tokens=old_tokens,
                    new_context_tokens=self.stats.context_tokens,
                    summary_length=len(summary),
                )

            case MiddlewareAction.CONTINUE:
                pass

    def _get_context(self) -> ConversationContext:
        return ConversationContext(
            messages=self.messages, stats=self.stats, config=self.config
        )

    def _get_extra_headers(self, provider: ProviderConfig) -> dict[str, str]:
        headers: dict[str, str] = {
            "user-agent": get_user_agent(provider.backend),
            "x-affinity": self.session_id,
        }
        if (
            provider.backend == Backend.MISTRAL
            and self._current_user_message_id is not None
        ):
            headers["metadata"] = json.dumps({
                "message_id": self._current_user_message_id
            })
        return headers

    async def _conversation_loop(self, user_msg: str) -> AsyncGenerator[BaseEvent]:
        user_message = LLMMessage(role=Role.user, content=user_msg)
        self.messages.append(user_message)
        self.stats.steps += 1
        self._current_user_message_id = user_message.message_id

        if user_message.message_id is None:
            raise AgentLoopError("User message must have a message_id")

        # Reset sticky error counters on every fresh user turn. Without
        # this, once `_total_error_rounds` hits the 3-round stop ceiling
        # (~45 API errors), it stays at 3 forever — every subsequent
        # user message immediately re-trips the ceiling on its first
        # API error and aborts. Users were stuck typing /clear (which
        # wipes the whole session) just to recover. The user has
        # manually intervened by typing again; they earn a fresh
        # error budget. The bad messages may also have been
        # dropped/compacted by the previous round's recovery path, so
        # the new turn often succeeds where the prior round couldn't.
        if getattr(self, "_total_error_rounds", 0) > 0:
            logger.warning(
                "[recovery] resetting _total_error_rounds=%d → 0 on new user turn",
                self._total_error_rounds,
            )
            self._total_error_rounds = 0

        # Flush the user message to disk RIGHT NOW, before the LLM call.
        # Without this, messages.jsonl only updates after the model yields
        # — for silent/slow prompts the user message is invisible to any
        # process tailing the session log (e.g. the stress harness),
        # which then thinks the prompt was never delivered and retries
        # or skips. Cheap: save_interaction only writes the delta.
        try:
            await self._save_messages()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session] early user-message flush failed: %s", exc)

        yield UserMessageEvent(content=user_msg, message_id=user_message.message_id)

        # === AUTO-CONTEXT ===
        # Auto-explore project files + inject relevant skill for the task type.
        # The model handles everything with subagents (v2.0.0 fixed delegation).
        _ar_t0 = time.perf_counter()
        await self._auto_route_task(user_msg)
        _ar_t1 = time.perf_counter()
        logger.warning("[TIMING] _auto_route_task: %.2fs", _ar_t1 - _ar_t0)

        # === AUTO-PREFETCH RETRIEVE ===
        # HLE Phase 1 finding (memory: project_graphrag_underused.md): Gemma 4
        # almost never calls retrieve() on its own for general-knowledge
        # questions — it defaults to web_search instead. So a curated
        # GraphRAG corpus is invisible to the model unless we surface it
        # automatically. This hook runs retrieve(query=user_msg[:300]) and,
        # if there are real hits (above a quality threshold), prepends them
        # as a system note BEFORE the first LLM call. Zero behavior change
        # when the index has nothing relevant; pure lift when it does.
        #
        # Disable both hooks under pytest. They'd otherwise inject noise
        # into the agent's message stream — a synthetic retrieve tool call
        # for auto-retrieve, and queue writes for curiosity — which breaks
        # tests that pin the exact event order or count messages.
        # Set DRYDOCK_AUTO_RETRIEVE=1 / DRYDOCK_CURIOSITY=1 explicitly in a
        # test if you want to exercise them.
        _under_pytest = "PYTEST_CURRENT_TEST" in os.environ

        # SOVEREIGN_PRD §5.7 acceptance #1: "retrieve called on ≥80% of HLE
        # questions before any content token". Default ON in production —
        # the prefetch is a no-op when no GraphRAG db exists (early return),
        # so users without a corpus are unaffected. Opt out with
        # DRYDOCK_AUTO_RETRIEVE=0.
        _auto_retrieve_default = "0" if _under_pytest else "1"
        if os.environ.get("DRYDOCK_AUTO_RETRIEVE", _auto_retrieve_default).strip().lower() not in ("0", "false", "no"):
            logger.warning("[AUTO-RETRIEVE] hook entry, query=%r", (user_msg or "")[:80])
            try:
                self._auto_prefetch_retrieve(user_msg)
            except Exception as _e:
                logger.warning("auto-prefetch retrieve failed (skipped): %s", _e, exc_info=True)

        # === CURIOSITY GAP LOGGING ===
        # SOVEREIGN_PRD §5.7 tier-2: extract candidate unfamiliar terms from
        # the user message and enqueue UNKNOWN_TERM curiosity items. The
        # queue is dedup'd by fingerprint over 7 days so the same recurring
        # term doesn't flood it. Disabled by setting DRYDOCK_CURIOSITY=0.
        # Off by default under pytest (see auto-retrieve note above).
        _curiosity_default = "0" if _under_pytest else "1"
        if os.environ.get("DRYDOCK_CURIOSITY", _curiosity_default).strip().lower() not in ("0", "false", "no"):
            try:
                self._log_curiosity_gaps(user_msg)
            except Exception as _e:
                logger.warning("curiosity gap logging failed (skipped): %s", _e)

        # === CONSTRAINT-SHAPE DETECTOR ===
        # The solve tool (Z3-backed) is the right answer for "find x such
        # that ...", optimization, prove-from-premises, mod-arithmetic, and
        # logic-puzzle questions. Gemma 4 doesn't reach for it on its own —
        # this hook recognises the shape and injects a worked-example
        # template the model can specialize. Off under pytest, on by default
        # in production. Opt out via DRYDOCK_CONSTRAINT_HINT=0.
        _constraint_hint_default = "0" if _under_pytest else "1"
        _constraint_hint_on = os.environ.get(
            "DRYDOCK_CONSTRAINT_HINT", _constraint_hint_default
        ).strip().lower() not in ("0", "false", "no")
        if _constraint_hint_on:
            try:
                from drydock.core.constraint_hint import (
                    detect_constraint_shape, build_hint,
                )
                hit = detect_constraint_shape(user_msg or "")
                if hit is not None:
                    label, example = hit
                    logger.warning(
                        "[CONSTRAINT-HINT] matched %s; injecting template",
                        label,
                    )
                    self._inject_system_note(build_hint(label, example))
            except Exception as _e:
                logger.warning(
                    "constraint hint failed (skipped): %s", _e, exc_info=True
                )

        # === AUTO-SOLVE (synthetic Z3 tool call) ===
        # The escalation level above the advisory constraint hint: when
        # the extractor produces a high-confidence ExtractResult AND Z3
        # can actually decide it, we run Z3 ourselves and inject a
        # synthetic solve() call + tool result. Models trust tool output
        # as authoritative — much stronger signal than a system note.
        # Same pattern as _auto_prefetch_retrieve for GraphRAG.
        # Off under pytest. Opt out via DRYDOCK_AUTO_SOLVE=0.
        _auto_solve_default = "0" if _under_pytest else "1"
        if _constraint_hint_on and os.environ.get(
            "DRYDOCK_AUTO_SOLVE", _auto_solve_default
        ).strip().lower() not in ("0", "false", "no"):
            try:
                from drydock.core.auto_solve import maybe_inject_auto_solve
                maybe_inject_auto_solve(self.messages, user_msg or "")
            except Exception as _e:
                logger.warning(
                    "auto-solve failed (skipped): %s", _e, exc_info=True
                )

        try:
            should_break_loop = False
            tool_turns = 0
            api_error_count = 0
            has_made_edit = False  # Track if model has used search_replace/write_file
            # Per-user-prompt wall-clock budget. Gemma 4 can spend 60+
            # minutes on a single prompt without closing it, but user
            # feedback (issue #9) showed 15 min was cutting off legitimate
            # "really difficult" builds. Bumped to 30 min — long enough
            # for a multi-file refactor, short enough that a runaway loop
            # still hands control back before the user gives up.
            # Admiral Phase 3a: per-(model, task) override if configured.
            PER_PROMPT_BUDGET_SEC = int(
                getattr(self, "_admiral_per_prompt_budget_sec", 30 * 60)
            )
            HARD_STOP_CALLS = int(
                getattr(self, "_admiral_hard_stop_tool_calls", 100)
            )
            WRAP_UP_WARN_AT = int(getattr(self, "_admiral_wrap_up_warn_at",
                int(os.environ.get("DRYDOCK_WRAP_UP_WARN_AT", 30))))
            STOP_NOW_WARN_AT = int(getattr(self, "_admiral_stop_now_warn_at",
                int(os.environ.get("DRYDOCK_STOP_NOW_WARN_AT", 60))))
            STOP_NOW_TIME_SEC = int(os.environ.get("DRYDOCK_STOP_NOW_TIME_SEC", "0"))
            TOOL_STOP_AFTER = int(os.environ.get("DRYDOCK_TOOL_STOP_AFTER", "0"))
            _prompt_start = time.perf_counter()
            _time_stop_injected = False
            _time_stop_escalated = False
            _tool_stop_injected = False
            logger.warning("[TIMING] entering conversation while loop")
            while not should_break_loop:
                # Drain any user messages typed while the agent was working.
                # Done BEFORE the turn counter increments and BEFORE middleware
                # runs so the new context is visible to both.
                self._drain_user_injections()
                # Loop protection: prevent infinite tool-call loops
                tool_turns += 1
                _wt0 = time.perf_counter()
                logger.warning("[TIMING] turn %d: starting", tool_turns)
                if tool_turns > MAX_TOOL_TURNS:
                    yield AssistantEvent(
                        content=f"\n\n[Maximum tool call limit ({MAX_TOOL_TURNS}) reached. Stopping.]\n",
                        stopped_by_middleware=True,
                    )
                    return

                # Progressive budget warnings — much tighter than before.
                # Gemma 4 routinely burns 30+ tool calls on a single
                # feature add (write→test→debug→edit→test cycles), which
                # looks healthy turn-by-turn but is a meandering loop in
                # user terms (12 prompts in 2+ hours observed). Push the
                # model to wrap up earlier.
                _elapsed = time.perf_counter() - _prompt_start
                if _elapsed > PER_PROMPT_BUDGET_SEC:
                    yield AssistantEvent(
                        content=(
                            f"\n\n[Drydock: {int(_elapsed/60)} minutes "
                            f"elapsed on this single prompt — over the "
                            f"{PER_PROMPT_BUDGET_SEC // 60}-min budget. "
                            "Stopping. Work done so far is on disk; "
                            "your next prompt can review or continue.]\n"
                        ),
                        stopped_by_middleware=True,
                    )
                    return
                # Time-based STOP_NOW: fires when wall-clock exceeds
                # DRYDOCK_STOP_NOW_TIME_SEC regardless of turn count.
                # Helps HLE batches where per-turn latency is 60-160s
                # and the turn-based STOP_NOW fires too late or not at all.
                #
                # IMPORTANT: check most-extreme condition FIRST. If a single
                # LLM generation spans all three thresholds (e.g. starts at
                # 200s, returns at 480s), the loop only gets one check at
                # 480s. The old if/elif order would fire the *first* injection
                # at 480s instead of the hard-stop. Reversed order ensures the
                # hard-stop always wins when we're past all thresholds.
                if (STOP_NOW_TIME_SEC > 0
                        and _elapsed > STOP_NOW_TIME_SEC + 120):
                    # Past all injection thresholds — hard-stop unconditionally.
                    yield AssistantEvent(
                        content=(
                            f"\n\n[Drydock: hard time limit ({int(_elapsed)}s) reached. "
                            "No final answer was provided before the deadline.]\n"
                        ),
                        stopped_by_middleware=True,
                    )
                    return
                elif (STOP_NOW_TIME_SEC > 0
                        and not _time_stop_escalated
                        and _elapsed > STOP_NOW_TIME_SEC + 60):
                    # Model made another tool call after the first STOP_NOW
                    # (or missed it entirely). Escalate with a forceful injection.
                    _time_stop_injected = True
                    _time_stop_escalated = True
                    _stop_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    self._inject_system_note(
                        f"URGENT: {int(_elapsed)}s elapsed. Do NOT call any more tools. "
                        "Emit a plain text response RIGHT NOW with your best answer. "
                        "If uncertain, still write an answer."
                        + (f" {_stop_suffix}" if _stop_suffix else "")
                    )
                elif (STOP_NOW_TIME_SEC > 0
                        and not _time_stop_injected
                        and _elapsed > STOP_NOW_TIME_SEC):
                    _time_stop_injected = True
                    _stop_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    self._inject_system_note(
                        f"Time limit reached: {int(_elapsed)}s elapsed on "
                        "this single request. STOP NOW. Emit a final text "
                        "response summarizing what you have or your best "
                        "guess."
                        + (f" {_stop_suffix}" if _stop_suffix else "")
                    )
                if (TOOL_STOP_AFTER > 0
                        and not _tool_stop_injected
                        and tool_turns >= TOOL_STOP_AFTER):
                    _tool_stop_injected = True
                    _stop_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    self._inject_system_note(
                        f"You have used {tool_turns} tool calls. "
                        "You may NOT call any more tools. "
                        "Your NEXT response must be plain text only — "
                        "write your best answer right now."
                        + (f" {_stop_suffix}" if _stop_suffix else "")
                    )
                elif (TOOL_STOP_AFTER > 0
                        and _tool_stop_injected
                        and tool_turns > TOOL_STOP_AFTER):
                    # Model called another tool after the stop note — force
                    # text-only on the next LLM call so it must emit an answer.
                    self._hle_force_text_only = True
                if tool_turns == WRAP_UP_WARN_AT:
                    self._inject_system_note(
                        f"You have used {tool_turns} tool calls on this "
                        "single user request. Start wrapping up — make "
                        "your next 3-5 calls count, then stop with a "
                        "summary so the user can review."
                    )
                elif tool_turns == STOP_NOW_WARN_AT:
                    _stop_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    self._inject_system_note(
                        f"You have used {tool_turns} tool calls on this "
                        "single request. STOP NOW. Emit a final text "
                        "response summarizing what you did (or what is "
                        f"blocked) so the user can take the next step."
                        + (f" {_stop_suffix}" if _stop_suffix else "")
                    )
                elif tool_turns >= HARD_STOP_CALLS:
                    # Hard end-of-turn: synthesize a user-facing message
                    # and stop. Was 50 but issue #9 showed "really
                    # difficult" tasks legitimately need more than 50
                    # tool calls. 100 preserves the runaway-loop safety
                    # while giving complex builds room to finish.
                    yield AssistantEvent(
                        content=(
                            f"\n\n[Drydock: stopped after {tool_turns} tool "
                            "calls on a single request — too long without "
                            "closing the turn. Returning control to the "
                            "user. The work done so far is on disk; your "
                            "next prompt can review or continue.]\n"
                        ),
                        stopped_by_middleware=True,
                    )
                    return
                if tool_turns in (75, 125, 175):
                    self._inject_system_note(
                        f"You have used {tool_turns}/{MAX_TOOL_TURNS} tool calls. "
                        "Wrap up your current task. If you are stuck in a loop, "
                        "stop and ask the user for clarification."
                    )

                _mw0 = time.perf_counter()
                result = await self.middleware_pipeline.run_before_turn(
                    self._get_context()
                )
                _mw1 = time.perf_counter()
                logger.warning("[TIMING] turn %d: middleware=%.2fs action=%s", tool_turns, _mw1 - _mw0, result.action)
                async for event in self._handle_middleware_result(result):
                    yield event

                if result.action == MiddlewareAction.STOP:
                    return

                self.stats.steps += 1
                user_cancelled = False
                try:
                    force_stopped = False
                    logger.warning("[TIMING] turn %d: calling _perform_llm_turn", tool_turns)
                    async for event in self._perform_llm_turn():
                        if is_user_cancellation_event(event):
                            user_cancelled = True
                        if isinstance(event, AssistantEvent) and event.stopped_by_middleware:
                            force_stopped = True
                        logger.warning("[TIMING] turn %d: yielding event type=%s", tool_turns, type(event).__name__)
                        yield event
                        await self._save_messages()
                    if force_stopped:
                        return
                    # Reset API error count on successful turn
                    api_error_count = 0
                except (RuntimeError, AgentLoopLLMResponseError) as e:
                    api_error_count += 1
                    if api_error_count > MAX_API_ERRORS:
                        # Track total error rounds — stop after 3 rounds
                        if not hasattr(self, '_total_error_rounds'):
                            self._total_error_rounds = 0
                        self._total_error_rounds += 1
                        if self._total_error_rounds >= 3:
                            # Hard-stop after 3 rounds of recovery attempts.
                            # Drop the LAST user→assistant block so the next
                            # user message doesn't immediately re-trigger the
                            # same broken state. Counter resets on the next
                            # _conversation_loop entry, so the user can just
                            # type the next message and continue without
                            # losing their entire context.
                            try:
                                # Find the last user message; truncate to it.
                                # Keep everything up to AND INCLUDING that
                                # message; drop the assistant garbage after.
                                last_user_idx = -1
                                for i in range(len(self.messages) - 1, -1, -1):
                                    if self.messages[i].role == Role.user:
                                        last_user_idx = i
                                        break
                                if last_user_idx >= 0 and last_user_idx < len(self.messages) - 1:
                                    kept = list(self.messages[: last_user_idx + 1])
                                    self.messages.reset(kept)
                                    logger.warning(
                                        "[recovery] hard-stop: dropped %d messages "
                                        "after last user turn (idx=%d)",
                                        len(self.messages) - last_user_idx - 1,
                                        last_user_idx,
                                    )
                            except Exception as _drop_err:  # noqa: BLE001
                                logger.warning(
                                    "[recovery] hard-stop drop failed: %s",
                                    _drop_err,
                                )
                            yield AssistantEvent(
                                content=(
                                    f"\n\n[Stopping after {self._total_error_rounds * MAX_API_ERRORS}+ "
                                    f"API errors. Conversation rolled back to your "
                                    f"last message — just type your next request "
                                    f"to continue. (Use /compact if context is "
                                    f"genuinely too long, /clear only to fully reset.)]\n"
                                ),
                            )
                            return

                        import asyncio as _aio
                        yield AssistantEvent(
                            content=f"\n\n[{api_error_count} consecutive API errors (round {self._total_error_rounds}/3). Compacting and retrying. Last error: {str(e)[:200]}]\n",
                        )
                        await _aio.sleep(5)
                        api_error_count = 0  # Reset — give it another chance
                        continue
                    # Check if the error is about invalid function/tool name
                    error_str = str(e)
                    if "Function name" in error_str or "function" in error_str.lower() and "must be" in error_str.lower():
                        # Model hallucinated a tool name — give it the correct list
                        available = ", ".join(sorted(self.tool_manager.available_tools.keys())[:15])
                        error_text = (
                            f"ERROR: You tried to call a tool that does not exist. "
                            f"Available tools: {available}. "
                            f"Use one of these exact tool names. "
                            f"For subagent delegation, use 'task'. For file search, use 'grep'."
                        )
                    elif ("context length" in error_str.lower()
                          or "maximum context" in error_str.lower()
                          or "400 bad request" in error_str.lower()
                          or "status: 400" in error_str.lower()
                          or "exceeds the available context" in error_str.lower()
                          or "error code: 400" in error_str.lower()
                          or "500 internal server error" in error_str.lower()
                          or "status: 500" in error_str.lower()
                          or "llm backend error" in error_str.lower()):
                        # Context limit or malformed request — aggressive recovery
                        # Step 0 (added 2026-05-09): if the error looks like a
                        # malformed tool call (most common 400 cause that ISN'T
                        # context-overflow), drop the offending assistant
                        # message + its orphaned tool-result follow-ups so the
                        # retry doesn't re-send the same bad payload. Without
                        # this, drydock would re-send the same broken
                        # tool_call N times until MAX_API_ERRORS gave up,
                        # leaving the user with a sticky banner that only
                        # /clear or session-restart could clear.
                        dropped_bad_tool_call = False
                        bad_call_indicators = (
                            "tool_call", "tool call", "function call",
                            "function.arguments", "arguments",
                            "invalid json", "json decode", "schema",
                            "validation error", "tool_use", "function name",
                        )
                        if any(ind in error_str.lower() for ind in bad_call_indicators):
                            try:
                                # Walk backward to the most recent assistant
                                # message with tool_calls — that's the payload
                                # vLLM rejected.
                                bad_idx = None
                                for i in range(len(self.messages) - 1, -1, -1):
                                    m = self.messages[i]
                                    if m.role == Role.assistant and getattr(m, "tool_calls", None):
                                        bad_idx = i
                                        break
                                if bad_idx is not None:
                                    # Drop the bad assistant message PLUS any
                                    # tool-role messages that followed it (they
                                    # reference tool_call_ids that no longer
                                    # exist; sending them alone is also a 400).
                                    new_msgs = list(self.messages[:bad_idx])
                                    self.messages.reset(new_msgs)
                                    dropped_bad_tool_call = True
                                    logger.info(
                                        "Auto-recovery: dropped bad tool-call "
                                        "message (idx=%d) and %d follow-ups",
                                        bad_idx, len(self.messages) - bad_idx
                                        if hasattr(self, "messages") else 0,
                                    )
                            except Exception as drop_err:
                                logger.debug("bad-tool-call drop failed: %s", drop_err)

                        try:
                            # First try: truncate old messages
                            for i, msg in enumerate(self.messages):
                                if i >= len(self.messages) - 4:
                                    break
                                if msg.role == Role.tool and hasattr(msg, 'content'):
                                    content = str(msg.content) if msg.content else ""
                                    if len(content) > 200:
                                        msg.content = content[:100] + "\n[truncated]"
                                elif msg.role == Role.assistant and hasattr(msg, 'content'):
                                    content = str(msg.content) if msg.content else ""
                                    if len(content) > 500:
                                        msg.content = content[:200] + "\n[truncated]"

                            # Second try: if messages > 20, keep only last 6
                            if len(self.messages) > 20:
                                first_user = None
                                for msg in self.messages:
                                    if msg.role == Role.user:
                                        first_user = msg
                                        break
                                kept = []
                                if first_user:
                                    kept.append(first_user)
                                kept.extend(self.messages[-5:])
                                self.messages.reset(kept)
                                logger.info("Emergency reset: kept first user + last 5 messages")
                        except Exception:
                            pass
                        if dropped_bad_tool_call:
                            # Detect JSON-truncation: llama.cpp returns this
                            # when max_tokens is too low and the tool call
                            # JSON gets cut off mid-string.
                            _trunc = (
                                "missing closing quote" in error_str.lower()
                                or (
                                    "parse error at" in error_str.lower()
                                    and "column" in error_str.lower()
                                )
                            )
                            if _trunc:
                                error_text = (
                                    "Your write_file content was too large — "
                                    "the server truncated the response mid-JSON "
                                    "(hit max_tokens). Split the file into "
                                    "smaller sections and write each with a "
                                    "separate write_file call (aim for ≤50 "
                                    "lines per call)."
                                )
                            else:
                                error_text = (
                                    "Your last tool call was rejected by the "
                                    "server (likely malformed arguments). "
                                    "Try a simpler form, or use a different tool."
                                )
                        else:
                            error_text = (
                                "Context compacted due to API error. "
                                "Continue with your task."
                            )
                    else:
                        error_text = f"API error occurred: {e}. Please continue with your task."
                    self._inject_system_note(error_text)
                    continue

                if not self.messages:
                    continue
                last_message = self.messages[-1]

                # Track edits — no circuit breakers, just track for has_made_edit
                if not has_made_edit:
                    for msg in reversed(self.messages[-5:]):
                        if msg.role == Role.assistant and msg.tool_calls:
                            for tc in msg.tool_calls:
                                if tc.function and tc.function.name in ("search_replace", "write_file"):
                                    has_made_edit = True
                                    break

                # Break when the model emits a text-only assistant
                # response (no tool calls). That means the model is done
                # with this user turn — return control so the user sees
                # the response and can send the next prompt.
                #
                # The previous condition `last_message.role != Role.tool
                # and tool_turns == 0` was unreachable: tool_turns is
                # incremented to ≥1 at the top of every iteration, so
                # the equality is never true after the first call. With
                # the auto-"Continue." injection in _sanitize_message_
                # ordering, the model re-ran forever on text-only
                # prompts; with that injection disabled (via
                # DRYDOCK_AUTO_CONTINUE_DISABLE=1) the model regenerated
                # the same text response until PER_PROMPT_BUDGET_SEC
                # timed out. Either way user turns never closed on a
                # "done" state. See stress_shakedown.py runs v3–v7 for
                # the full wedge picture.
                #
                # If Gemma 4 emits intermediate summaries without tool
                # calls ("Wrote X, now I'll write Y") the user will see
                # partial progress and have to prompt "continue". That's
                # an acceptable cost compared to the forever-loop.
                should_break_loop = (
                    last_message.role == Role.assistant
                    and not last_message.tool_calls
                )

                # No circuit breakers, no loop detection, no forced nudges.
                # The model works on its own. The only hard stop is MAX_TOOL_TURNS.

                if user_cancelled:
                    return

        finally:
            await self._save_messages()

            # Session quality check REMOVED — was blocking workflow

    async def _perform_llm_turn(self) -> AsyncGenerator[BaseEvent, None]:
        def _dbg(msg: str) -> None:
            try:
                with open("/tmp/drydock_stall_debug.log", "a") as _f:
                    _f.write(msg + "\n")
            except Exception:
                pass

        # One LLM call, with up to MAX_STALL_RETRIES inline retries on
        # empty responses (no content AND no tool_calls). After each
        # empty, pop it, inject a nudge, and re-call within the same
        # turn so the model gets a real chance to recover BEFORE
        # control returns to the outer loop (which would otherwise exit
        # on the empty + end the user turn).
        MAX_STALL_RETRIES = 3
        for _stall_attempt in range(MAX_STALL_RETRIES + 1):
            if self.enable_streaming:
                async for event in self._stream_assistant_events():
                    yield event
            else:
                assistant_event = await self._get_assistant_event()
                if assistant_event.content:
                    yield assistant_event

            if not self.messages:
                _dbg("[STALL-DEBUG] no messages")
                return
            last_message = self.messages[-1]
            _dbg(
                f"[STALL-DEBUG] attempt={_stall_attempt} role={last_message.role} "
                f"content_len={len(last_message.content or '')} "
                f"has_tool_calls={bool(last_message.tool_calls)} "
                f"msgs={len(self.messages)}"
            )

            # If productive (has content OR tool calls), exit retry loop.
            if last_message.content or last_message.tool_calls:
                break

            # Empty response — try to recover inline.
            if _stall_attempt >= MAX_STALL_RETRIES:
                _dbg(f"[STALL-DEBUG] max retries ({MAX_STALL_RETRIES}) exhausted; injecting fallback")
                # Replace the silent empty message with visible text so the
                # harness and the user both see a clean end-of-turn rather
                # than a frozen TUI waiting for content that never arrives.
                last_message.content = (
                    "[Drydock: model returned an empty response after "
                    f"{MAX_STALL_RETRIES} retries. Please rephrase your "
                    "request or use /clear to reset context.]"
                )
                yield AssistantEvent(content=last_message.content)
                break
            prev_role = self.messages[-2].role if len(self.messages) >= 2 else None
            if prev_role not in (Role.tool, Role.user):
                _dbg(f"[STALL-DEBUG] prev_role={prev_role} not recoverable")
                break

            _dbg(f"[STALL-DEBUG] inline retry #{_stall_attempt + 1} (prev={prev_role})")
            # Pop the empty assistant; inject an escalating nudge.
            self.messages.pop()
            # Detect what the previous tool was so the nudge can steer
            # the model toward the RIGHT next action. Suggesting read_file
            # when the model just stalled after read_file reinforces the loop.
            prev_tool_name: str | None = None
            if prev_role == Role.tool and len(self.messages) >= 2:
                # messages[-1] is now the tool result; messages[-2] is the
                # assistant that called the tool.
                assistant_msg = self.messages[-2]
                if (assistant_msg.role == Role.assistant
                        and assistant_msg.tool_calls):
                    prev_tool_name = assistant_msg.tool_calls[-1].function.name if assistant_msg.tool_calls[-1].function else None
            _readonly_tools = {"read_file", "grep", "glob", "ls", "pwd",
                               "ralph_repo_index", "ralph_file_summary",
                               "retrieve", "search_files", "lsp",
                               "web_search", "web_fetch"}
            _write_tools = {"write_file", "search_replace"}
            _prev_was_read = prev_tool_name in _readonly_tools
            _prev_was_write = prev_tool_name in _write_tools
            # Detect if prior write_file failed due to missing path argument.
            _prev_tool_result = ""
            if prev_role == Role.tool and self.messages:
                _prev_tool_result = str(self.messages[-1].content or "")
            _prev_write_path_error = (
                prev_tool_name == "write_file"
                and "empty path" in _prev_tool_result
            )
            # Detect if prior tool was a hallucinated/suppressed tool call.
            # Check against the live tool registry — the "does not exist" string
            # is only in the system note, not the tool result, so string matching fails.
            _prev_was_hallucinated = (
                prev_tool_name is not None
                and prev_tool_name not in self.tool_manager.available_tools
            )
            # Detect successful write (no error keywords in result).
            _prev_write_success = (
                _prev_was_write
                and not _prev_write_path_error
                and "Error" not in _prev_tool_result
                and "error" not in _prev_tool_result[:50]
            )
            # Detect bash that returned "nothing to commit" / "working tree clean"
            # → model is done; stall nudge should say so, not "continue working".
            _prev_bash_nothing_to_commit = (
                prev_tool_name in ("bash", "run_command")
                and (
                    "nothing to commit" in _prev_tool_result
                    or "working tree clean" in _prev_tool_result
                    or "nothing added to commit" in _prev_tool_result
                )
            )
            # Detect bash that returned a successful git commit output.
            # Signature: "[branch hash] message\n N file(s) changed".
            # Without this, the model stalls after commit, gets "Continue working",
            # then re-commits — wastes a round and adds a confusing duplicate commit.
            import re as _re
            _prev_bash_commit_succeeded = (
                prev_tool_name in ("bash", "run_command")
                and bool(_re.search(r"\[[\w/]+ [0-9a-f]{4,}\]", _prev_tool_result))
                and "file" in _prev_tool_result
                and "changed" in _prev_tool_result
            )
            # Detect bash that returned an error/traceback.
            # These stalls fire as empty_after_tool:bash :: source=canned — the
            # generic note doesn't tell the model what to DO with the error.
            _prev_bash_had_error = (
                prev_tool_name in ("bash", "run_command")
                and not _prev_bash_nothing_to_commit
                and not _prev_bash_commit_succeeded
                and bool(_re.search(
                    r"(Error|error|Traceback|FAILED|exit code [1-9]|command not found)",
                    _prev_tool_result
                ))
            )
            # Detect bash that returned non-empty output without an error.
            # Model stalls instead of using the output to write or fix code.
            _prev_bash_had_output = (
                prev_tool_name in ("bash", "run_command")
                and not _prev_bash_nothing_to_commit
                and not _prev_bash_commit_succeeded
                and not _prev_bash_had_error
                and bool(_prev_tool_result.strip())
            )
            if _stall_attempt == 0:
                if _tool_stop_injected:
                    _fa_suffix = os.environ.get(
                        "DRYDOCK_STOP_NOW_SUFFIX",
                        "End with 'FINAL ANSWER: <answer>'.",
                    )
                    note = (
                        "STOP THINKING. Do NOT use any tools. "
                        "Write your best answer as plain text RIGHT NOW. "
                        + _fa_suffix
                    )
                elif _prev_write_path_error:
                    note = (
                        "Your write_file call failed because the path argument was empty. "
                        "Retry write_file RIGHT NOW with the correct path. "
                        "Example: write_file(path='package/module.py', content='...'). "
                        "Do NOT send an empty response — call write_file with a path."
                    )
                elif _prev_was_hallucinated:
                    note = (
                        f"The tool '{prev_tool_name}' does not exist — stop calling it. "
                        "Call glob(pattern='**/*.py') NOW to list project files, "
                        "or grep(pattern='...') to search content. "
                        "Do NOT send an empty response."
                    )
                elif prev_tool_name == "ralph_repo_index":
                    note = (
                        "You indexed the repository but produced no output. "
                        "Now write a text answer to the user's question, "
                        "or call read_file to inspect a specific file. "
                        "Do NOT call ralph_repo_index again."
                    )
                elif _prev_was_read:
                    _tool_name_str = prev_tool_name or "read_file"
                    _generic_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    note = (
                        f"You called {_tool_name_str} but produced no output. "
                        f"Now respond in text — write your answer or make changes. "
                        f"Do NOT call {_tool_name_str} again."
                        + (f" {_generic_suffix}" if _generic_suffix else "")
                    )
                elif _prev_write_success:
                    note = (
                        "You wrote a file successfully. Continue to the NEXT step: "
                        "write the next file in your plan, or run bash to test what "
                        "you have built so far. Do NOT re-read files you just wrote."
                    )
                elif _prev_bash_nothing_to_commit:
                    note = (
                        "The git working tree is clean — your commit already succeeded. "
                        "The task is COMPLETE. Respond with a short summary of what you did "
                        "and stop. Do NOT run another git commit or git add."
                    )
                elif _prev_bash_commit_succeeded:
                    note = (
                        "Your git commit succeeded. The task is COMPLETE. "
                        "Respond with a short summary of what you changed and stop. "
                        "Do NOT run git add or git commit again."
                    )
                elif _prev_bash_had_error:
                    note = (
                        "The command returned an error. Read the error message above, "
                        "then fix the code with search_replace or write_file, or try a "
                        "different command. Do NOT re-run the same failing command."
                    )
                elif _prev_bash_had_output:
                    note = (
                        "The command ran and returned output. Use that output now: "
                        "write or update code files, fix any issues shown, or respond "
                        "to the user. Do NOT re-run the same command."
                    )
                else:
                    _generic_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    note = (
                        "Continue working. Use a tool (write_file, "
                        "search_replace, bash, glob, grep) or state "
                        "your plan in text."
                        + (f" {_generic_suffix}" if _generic_suffix else "")
                    )
            elif _stall_attempt == 1:
                if _tool_stop_injected:
                    _fa_suffix = os.environ.get(
                        "DRYDOCK_STOP_NOW_SUFFIX",
                        "End with 'FINAL ANSWER: <answer>'.",
                    )
                    note = (
                        "STOP. Do NOT call any tools. "
                        "Write your answer as plain text RIGHT NOW. "
                        + _fa_suffix
                    )
                elif prev_tool_name == "ralph_repo_index":
                    note = (
                        "You sent an empty response after indexing the repository. "
                        "Respond in TEXT now — answer the user's question directly, "
                        "or state in one sentence why you cannot proceed. "
                        "Do NOT call ralph_repo_index again."
                    )
                elif _prev_was_read:
                    _tool_name_str = prev_tool_name or "read_file"
                    _generic_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    note = (
                        f"You sent an empty response after calling {_tool_name_str}. "
                        f"Respond in text now — write your answer or apply what you read. "
                        f"Do NOT call {_tool_name_str} again."
                        + (f" {_generic_suffix}" if _generic_suffix else "")
                    )
                elif _prev_was_write:
                    _tool_name_str = prev_tool_name or "write_file"
                    note = (
                        f"You sent an empty response after {_tool_name_str}. "
                        "Write the NEXT file in your plan NOW with write_file, "
                        "or run bash to test what you have built. "
                        "Do NOT send another empty response."
                    )
                elif _prev_bash_had_error:
                    note = (
                        "Second empty response after a bash error. "
                        "Fix the error NOW with search_replace or write_file. "
                        "Do NOT re-run the same failing command."
                    )
                elif _prev_bash_had_output:
                    note = (
                        "Second empty response after bash output. "
                        "Use the output now — write code with write_file, "
                        "fix issues with search_replace, or respond to the user. "
                        "Do NOT re-run the same command."
                    )
                else:
                    _generic_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    note = (
                        "You sent an empty response. Call a tool now "
                        "(write_file, search_replace, bash, read_file, glob) "
                        "OR explicitly say you are done with this task."
                        + (f" {_generic_suffix}" if _generic_suffix else "")
                    )
            else:
                if _tool_stop_injected:
                    _fa_suffix = os.environ.get(
                        "DRYDOCK_STOP_NOW_SUFFIX",
                        "End with 'FINAL ANSWER: <answer>'.",
                    )
                    note = (
                        "FINAL WARNING. You have sent multiple empty responses. "
                        "Do NOT use any tools. Write your answer NOW. "
                        + _fa_suffix
                    )
                elif prev_tool_name == "ralph_repo_index":
                    note = (
                        "THIRD empty response after ralph_repo_index. "
                        "Stop — write a text reply to the user RIGHT NOW. "
                        "If you cannot answer, say 'I was unable to find that information' "
                        "and stop. Do NOT call ralph_repo_index or any other tool."
                    )
                elif prev_tool_name in _readonly_tools:
                    _tool_name_str = prev_tool_name or "read_file"
                    _generic_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    note = (
                        f"THIRD empty response after {_tool_name_str}. "
                        "Stop — respond in text with your analysis or best answer. "
                        f"Do NOT call {_tool_name_str} again."
                        + (f" {_generic_suffix}" if _generic_suffix else "")
                    )
                elif _prev_was_write:
                    _tool_name_str = prev_tool_name or "write_file"
                    note = (
                        f"THIRD empty response after {_tool_name_str}. "
                        "Call bash NOW to run the tests or verify what you built, "
                        "or write the next required file. "
                        "Do NOT send another empty response."
                    )
                elif _prev_bash_had_error:
                    note = (
                        "THIRD empty response after a bash error. "
                        "Fix the error NOW — call search_replace or write_file to "
                        "correct the broken code, or respond in text with what went wrong. "
                        "Do NOT run the same failing command again."
                    )
                elif _prev_bash_had_output:
                    note = (
                        "THIRD empty response after bash output. "
                        "Act on the output NOW — write or fix code with write_file "
                        "or search_replace, or respond in one sentence. "
                        "Do NOT re-run the same command."
                    )
                else:
                    _generic_suffix = os.environ.get("DRYDOCK_STOP_NOW_SUFFIX", "")
                    note = (
                        "You have sent 3 empty responses in a row for "
                        "this user request. Respond with either (a) a "
                        "tool call to make progress, or (b) one "
                        "sentence explaining why you cannot proceed."
                        + (f" {_generic_suffix}" if _generic_suffix else "")
                    )
            self._inject_system_note(note)
            logger.info(
                "Empty-response stall (inline retry %d/%d, prev=%s)",
                _stall_attempt + 1, MAX_STALL_RETRIES, prev_role,
            )
            # When tool-stop is active, force text-only again on the
            # stall-retry LLM call.  _hle_force_text_only was consumed
            # (cleared) at the previous LLM call boundary, so without
            # this the stall-retry call gets tool_choice="auto" and the
            # model loops back to calling tools instead of answering.
            if _tool_stop_injected:
                self._hle_force_text_only = True
            # Loop back to re-call the LLM.
            continue

        # (Old stall check removed — now handled inline above in the
        # retry loop, which re-calls the LLM after each empty rather
        # than returning control to the outer loop that would exit on
        # empty assistant + user-role precursor.)

        # Detect repetitive text generation (Gemma 4 sometimes loops text within one response)
        if last_message.content and len(last_message.content) > 200:
            text = last_message.content
            # Check if any sentence repeats 3+ times.
            # Threshold 15: catches short repeated phrases like "(Wait, I'll call the tool."
            # which split to 28-char fragments — previously filtered by the old > 30 threshold.
            sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 15]
            if sentences:
                from collections import Counter
                sentence_counts = Counter(sentences)
                most_common, count = sentence_counts.most_common(1)[0]
                if count >= 3:
                    # Truncate to first occurrence + note
                    first_end = text.find(most_common) + len(most_common) + 1
                    last_message.content = text[:first_end].rstrip()
                    logger.info("Truncated repetitive text generation (%d repeats of '%s...')", count, most_common[:40])

        parsed = self.format_handler.parse_message(last_message)
        resolved = self.format_handler.resolve_tool_calls(parsed, self.tool_manager)

        if not resolved.tool_calls and not resolved.failed_calls and not resolved.suppressed_failures:
            return

        async for event in self._handle_tool_calls(resolved):
            yield event

    def _build_tool_call_events(
        self, tool_calls: list[ToolCall] | None, emitted_ids: set[str]
    ) -> Generator[ToolCallEvent, None, None]:
        for tc in tool_calls or []:
            if tc.id is None or not tc.function.name:
                continue
            if tc.id in emitted_ids:
                continue

            tool_class = self.tool_manager.available_tools.get(tc.function.name)
            if tool_class is None:
                continue

            yield ToolCallEvent(
                tool_call_id=tc.id,
                tool_call_index=tc.index,
                tool_name=tc.function.name,
                tool_class=tool_class,
            )

    async def _stream_assistant_events(
        self,
    ) -> AsyncGenerator[AssistantEvent | ReasoningEvent | ToolCallEvent]:
        message_id: str | None = None
        emitted_tool_call_ids = set[str]()

        async for chunk in self._chat_streaming():
            if message_id is None:
                message_id = chunk.message.message_id

            for event in self._build_tool_call_events(
                chunk.message.tool_calls, emitted_tool_call_ids
            ):
                emitted_tool_call_ids.add(event.tool_call_id)
                yield event

            if chunk.message.reasoning_content:
                yield ReasoningEvent(
                    content=chunk.message.reasoning_content, message_id=message_id
                )

            if chunk.message.content:
                yield AssistantEvent(
                    content=chunk.message.content, message_id=message_id
                )

    async def _get_assistant_event(self) -> AssistantEvent:
        llm_result = await self._chat()
        return AssistantEvent(
            content=llm_result.message.content or "",
            message_id=llm_result.message.message_id,
        )

    async def _emit_failed_tool_events(
        self, failed_calls: list[FailedToolCall]
    ) -> AsyncGenerator[ToolResultEvent]:
        for failed in failed_calls:
            error_msg = f"<{TOOL_ERROR_TAG}>{failed.tool_name}: {failed.error}</{TOOL_ERROR_TAG}>"
            yield ToolResultEvent(
                tool_name=failed.tool_name,
                tool_class=None,
                error=error_msg,
                tool_call_id=failed.call_id,
            )
            self.stats.tool_calls_failed += 1
            self.messages.append(
                self.format_handler.create_failed_tool_response_message(
                    failed, error_msg
                )
            )

    def _get_attempted_summary(self) -> str:
        """Build a summary of what the agent has already tried."""
        if not self._tool_call_history:
            return ""
        attempts = []
        for sig, (count, result) in self._tool_call_history.items():
            if count >= 2:
                attempts.append(f"  - Ran {count}x, result: {result[:80]}")
        if attempts:
            return "ALREADY ATTEMPTED (do NOT repeat):\n" + "\n".join(attempts[:10])
        return ""

    def _circuit_breaker_check(self, tool_call: ResolvedToolCall) -> str | None:
        """Block exact-duplicate tool calls after a high threshold.

        Re-enabled v2.6.102 after stress session 20260415_171815 hit
        91× identical search_replace with the same content
        (same SEARCH/REPLACE block, same file). The per-tool mute
        only lasts 1 turn so the model resumed immediately. The
        token-level sampling bumps don't help when the model picks
        the SAME serialized args every time.

        Thresholds tuned for 45-prompt session windows (stress sessions
        reset every 45 prompts — a threshold of 12 never fires in practice
        because the model loops 4-8 times then moves on; admiral advisories
        fire at 3 but the model ignores them):
          search_replace, write_file, bash: after 8 identical calls
          read_file, grep, glob, ls: after 5 identical calls
        """
        args_str = json.dumps(tool_call.args_dict, sort_keys=True, default=str)
        sig = hashlib.sha256(
            f"{tool_call.tool_name}:{args_str}".encode()
        ).hexdigest()
        count, last_result = self._tool_call_history.get(sig, (0, ""))
        tool_name = tool_call.tool_name
        is_readonly = tool_name in ("grep", "read_file", "glob", "ls")
        threshold = 5 if is_readonly else 8
        if count < threshold:
            return None
        # Increment so the count escalates on every repeated fire, giving the
        # model a growing signal that nothing has changed.  Preserve last_result
        # so the message always shows the real bash/tool output, not a prior NOTE.
        self._tool_call_history[sig] = (count + 1, last_result)
        # For read-only tools include the full cached content so the model
        # has the data it needs and doesn't retry just to see more output.
        result_preview = last_result if is_readonly else last_result[:200]
        return (
            f"NOTE: this exact call to `{tool_name}` has been made "
            f"{count} times this session with identical arguments. "
            f"Last result:\n{result_preview}\n\n"
            f"The result will not change on a {count + 1}th attempt. "
            f"Move on — call a DIFFERENT tool, use DIFFERENT arguments, "
            f"or end your turn with a text summary so the user can "
            f"take the next step."
        )

    def _circuit_breaker_check_FULL(self, tool_call: ResolvedToolCall) -> str | None:
        """Block exact-duplicate tool calls. Returns cached result or None.

        Thresholds:
        - Read-only tools (grep, read_file, ls, pwd, git status): block after 4
        - Write/edit tools (search_replace, write_file): block after 2
        - Other (bash with commands): block after 3
        """
        args_str = json.dumps(tool_call.args_dict, sort_keys=True, default=str)
        sig = hashlib.sha256(
            f"{tool_call.tool_name}:{args_str}".encode()
        ).hexdigest()

        count, last_result = self._tool_call_history.get(sig, (0, ""))
        is_failed = last_result.startswith("FAILED:") if last_result else False

        # Block failed commands after 2 repeats.
        # Block SUCCESSFUL commands after 4 repeats — the model should not
        # run the exact same command with the exact same args 5+ times.
        # Read-only checks (ls, pwd, git status) get a higher threshold.
        tool_name = tool_call.tool_name
        is_readonly = tool_name in ("grep", "read_file", "glob", "ls")
        success_threshold = 6 if is_readonly else 4

        if is_failed and count >= 2:
            pass  # will be blocked below
        elif not is_failed and count >= success_threshold:
            pass  # will be blocked below
        else:
            return None

        if count >= 2:
            attempted = self._get_attempted_summary()
            msg = (
                f"CIRCUIT BREAKER: You already ran `{tool_call.tool_name}` with these "
                f"exact arguments {count} times and got the same result each time.\n\n"
                f"Previous result: {last_result[:200]}\n\n"
                f"{attempted}\n\n"
                f"STOP repeating. You MUST try something DIFFERENT:\n"
                f"- Different arguments or search terms\n"
                f"- A completely different tool\n"
                f"- Ask the user for clarification"
            )

            # Suggest using /consult if a consultant model is configured
            try:
                from drydock.core.consultant import is_consultant_available
                if is_consultant_available():
                    msg += (
                        "\n\nTIP: A consultant model is available. "
                        "The user can type `/consult <question>` to ask it for advice."
                    )
            except Exception:
                pass

            return msg
        return None

    def _circuit_breaker_record(self, tool_call: ResolvedToolCall, result_text: str) -> None:
        """Record a tool call execution for circuit breaker tracking."""
        args_str = json.dumps(tool_call.args_dict, sort_keys=True, default=str)
        sig = hashlib.sha256(
            f"{tool_call.tool_name}:{args_str}".encode()
        ).hexdigest()
        count, _ = self._tool_call_history.get(sig, (0, ""))
        # Store more content for read-only tools so the NOTE advisory
        # can include enough context for the model to act on it.
        tool_name = tool_call.tool_name
        is_readonly = tool_name in ("grep", "read_file", "glob", "ls")
        store_limit = 2000 if is_readonly else 500
        self._tool_call_history[sig] = (count + 1, result_text[:store_limit])

    async def _process_one_tool_call(
        self, tool_call: ResolvedToolCall
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent]:
        # Circuit breaker: block exact duplicate calls after 2 attempts.
        # CLAUDE.md rule: advisory only, NEVER blocking. Loop detection
        # nudges the model but must never stop the session — only
        # MAX_TOOL_TURNS (200) is a hard stop. See the 2026-04-16 stress
        # run where FORCED STOP poisoned every subsequent prompt.
        if blocked := self._circuit_breaker_check(tool_call):
            self._consecutive_circuit_breaker_fires += 1
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                error=blocked,
                tool_call_id=tool_call.call_id,
            )
            self._handle_tool_response(tool_call, blocked, "failure")
            return
        else:
            # Reset consecutive fires when a non-blocked call happens
            self._consecutive_circuit_breaker_fires = 0

        try:
            tool_instance = self.tool_manager.get(tool_call.tool_name)
        except Exception as exc:
            error_msg = (
                f"Error getting tool '{tool_call.tool_name}': {exc}. "
                f"Available tools: bash, grep, read_file, write_file, search_replace, "
                f"todo, ask_user_question, task. Use one of these — do NOT invent tool names."
            )
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                error=error_msg,
                tool_call_id=tool_call.call_id,
            )
            self._handle_tool_response(tool_call, error_msg, "failure")
            return

        decision = await self._should_execute_tool(
            tool_instance, tool_call.validated_args, tool_call.call_id
        )

        if decision.verdict == ToolExecutionResponse.SKIP:
            self.stats.tool_calls_rejected += 1
            skip_reason = decision.feedback or str(
                get_user_cancellation_message(
                    CancellationReason.TOOL_SKIPPED, tool_call.tool_name
                )
            )
            # Add alternative suggestions so model can adjust strategy
            alternatives = {
                "write_file": "Try search_replace to modify existing files.",
                "search_replace": "Try write_file to create the file, or read_file first to get exact text.",
                "bash": "Try read_file + search_replace for code changes.",
                "task": "Try grep + read_file to explore manually.",
            }
            alt = alternatives.get(tool_call.tool_name, "")
            if alt:
                skip_reason += f"\n\n{alt}"
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                skipped=True,
                skip_reason=skip_reason,
                tool_call_id=tool_call.call_id,
            )
            self._handle_tool_response(tool_call, skip_reason, "skipped", decision)
            return

        self.stats.tool_calls_agreed += 1

        try:
            start_time = time.perf_counter()
            result_model = None
            async for item in tool_instance.invoke(
                ctx=InvokeContext(
                    tool_call_id=tool_call.call_id,
                    agent_manager=self.agent_manager,
                    session_dir=self.session_logger.session_dir,
                    entrypoint_metadata=self.entrypoint_metadata,
                    approval_callback=self.approval_callback,
                    user_input_callback=self.user_input_callback,
                    sampling_callback=self._sampling_handler,
                    plan_file_path=self._plan_session.plan_file_path,
                    switch_agent_callback=self.switch_agent,
                    read_file_state=self._read_file_state,
                ),
                **tool_call.args_dict,
            ):
                if isinstance(item, ToolStreamEvent):
                    yield item
                else:
                    result_model = item

            duration = time.perf_counter() - start_time
            if result_model is None:
                raise ToolError("Tool did not yield a result")

            result_dict = result_model.model_dump()
            text = "\n".join(f"{k}: {v}" for k, v in result_dict.items())

            # After task subagent finishes (completed or cancelled), nudge the
            # model to continue — Gemma 4 produces an empty turn without this.
            if tool_call.tool_name == "task":
                if result_dict.get("completed"):
                    self._inject_system_note(
                        "Task complete. Continue with your next step — call the next tool now."
                    )
                else:
                    self._inject_system_note(
                        "Task subagent stopped. Continue with your current goal — call the next tool now."
                    )

            # After a successful bash test of built code, nudge to wrap up
            if tool_call.tool_name in ("bash", "run_command"):
                self._successful_test_runs += 1
                if self._successful_test_runs >= 3:
                    self._inject_system_note(
                        "Your project is WORKING. You have verified it successfully. "
                        "STOP testing. Summarize what you built and tell the user "
                        "how to use it. Do NOT run any more bash commands."
                    )

            # Record for circuit breaker
            self._circuit_breaker_record(tool_call, text)
            self._handle_tool_response(
                tool_call, text, "success", decision, result_dict
            )
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                result=result_model,
                duration=duration,
                tool_call_id=tool_call.call_id,
            )
            self.stats.tool_calls_succeeded += 1

        except asyncio.CancelledError:
            cancel = str(
                get_user_cancellation_message(CancellationReason.TOOL_INTERRUPTED)
            )
            # Record cancelled calls in the circuit-breaker history too.
            # Without this the model can spin forever on cancelled calls
            # (observed in stress sessions: 15+ identical read_file all
            # returning <user_cancellation> with no count incrementing).
            self._circuit_breaker_record(tool_call, f"CANCELLED: {cancel[:200]}")
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                error=cancel,
                tool_call_id=tool_call.call_id,
            )
            self._handle_tool_response(tool_call, cancel, "failure", decision)
            raise

        except (ToolError, ToolPermissionError) as exc:
            error_msg = f"<{TOOL_ERROR_TAG}>{tool_instance.get_name()} failed: {exc}</{TOOL_ERROR_TAG}>"

            # Record FAILED calls in circuit breaker too — prevents repeating
            # the same failing command (e.g., pip install -r requirements.txt x5)
            self._circuit_breaker_record(tool_call, f"FAILED: {str(exc)[:200]}")

            # RECOVERY: Warn when editing test files
            if tool_call.tool_name == "search_replace":
                try:
                    sr_args = json.loads(tool_call.raw_arguments or "{}")
                    sr_path = sr_args.get("file_path", sr_args.get("path", ""))
                    if sr_path and ("/test_" in sr_path or "/tests/" in sr_path or sr_path.endswith("_test.py")):
                        error_msg += (
                            "\n\n[WARNING: You are editing a TEST file. "
                            "The bug is in LIBRARY SOURCE code, not tests. "
                            "Use grep to find the corresponding source file and edit that instead.]"
                        )
                except (json.JSONDecodeError, AttributeError):
                    pass

            # RECOVERY: Add actionable guidance for common tool failures
            if tool_call.tool_name == "search_replace" and "not found" in str(exc).lower():
                # Try to extract the file path from the tool args
                sr_file_hint = ""
                try:
                    sr_args = json.loads(tool_call.raw_arguments or "{}")
                    sr_path = sr_args.get("file_path", sr_args.get("path", ""))
                    if sr_path:
                        sr_file_hint = (
                            f" Also verify you are editing the CORRECT file. "
                            f"You targeted '{sr_path}' — the function you want might exist in a "
                            f"different module at a deeper or shallower path. "
                            f"Use grep to search for the function/class name across the codebase to confirm."
                        )
                except (json.JSONDecodeError, AttributeError):
                    pass
                # AUTO-READ: Automatically read the target file so the model has
                # the actual content — don't just tell it to read, DO it for it
                auto_read_content = ""
                try:
                    sr_args = json.loads(tool_call.raw_arguments or "{}")
                    sr_path = sr_args.get("file_path", sr_args.get("path", ""))
                    if sr_path and Path(sr_path).exists():
                        with open(sr_path, "r", encoding="utf-8", errors="replace") as f:
                            lines = f.readlines()
                        # Show first 50 lines or the whole file if small
                        preview_lines = lines[:50]
                        numbered = [f"{i+1}\t{line.rstrip()}" for i, line in enumerate(preview_lines)]
                        auto_read_content = (
                            f"\n\nAUTO-READ of {sr_path} (first {len(preview_lines)} lines):\n"
                            + "\n".join(numbered)
                        )
                        if len(lines) > 50:
                            auto_read_content += f"\n[... {len(lines) - 50} more lines]"
                except Exception:
                    pass
                error_msg += (
                    "\n\n[RECOVERY: Your search text didn't match the file contents. "
                    "The actual file content is shown below — use EXACT text from it for your next edit."
                    f"{sr_file_hint}]"
                    f"{auto_read_content}"
                )
            elif tool_call.tool_name == "search_replace" and "multiple" in str(exc).lower():
                error_msg += (
                    "\n\n[RECOVERY: Your search text matches multiple locations. "
                    "Add more surrounding context lines to old_str to make it unique.]"
                )

            # RECOVERY: Relative import error — tell model to use absolute imports or -m
            if tool_call.tool_name in ("bash", "run_command") and "relative import with no known parent" in str(exc):
                self._inject_system_note(
                    "The error 'relative import with no known parent package' means you are "
                    "running a package file directly (python3 pkg/file.py). Fix: either "
                    "(1) change 'from .module import X' to 'from pkg.module import X' (absolute imports), or "
                    "(2) run with 'python3 -m pkg' instead of 'python3 pkg/file.py'. "
                    "Use search_replace to change the imports to absolute imports NOW."
                )

            # RECOVERY: After bash failure with traceback, extract file/line and
            # inject a STRONG system note (not just error text) to force read→fix
            if tool_call.tool_name in ("bash", "run_command") and "Traceback" in str(exc):
                import re
                tb_matches = re.findall(r'File "([^"]+)", line (\d+)', str(exc))
                if tb_matches:
                    tb_file, tb_line = tb_matches[-1]
                    if not tb_file.startswith("/home") and "site-packages" not in tb_file:
                        error_msg += (
                            f"\n\n[NEXT STEP: Read {tb_file} around line {tb_line} "
                            f"with read_file, then fix it with search_replace.]"
                        )
                        # Also inject as system note — harder for model to ignore
                        self._inject_system_note(
                            f"STOP running bash. The error is at {tb_file}:{tb_line}. "
                            f"Use read_file to read that file, then search_replace to fix the bug. "
                            f"Do NOT run another bash command until you have fixed the code."
                        )

            # RECOVERY: hard-blocked duplicate write_file. When write_file raises
            # "BLOCKED: ... has been called N times with IDENTICAL content", the
            # model has a history full of identical no-op writes. Prune those from
            # message history so the model's next turn sees a cleaner context and
            # is less likely to re-trigger the same loop.
            #
            # Pruning is safe here because the duplicates are by definition no-ops
            # against the file on disk — deleting the history preserves actual
            # state. We keep the most recent write attempt (which is the one that
            # just got blocked) so the model sees the error.
            if (
                tool_call.tool_name == "write_file"
                and "BLOCKED:" in str(exc)
                and "IDENTICAL content" in str(exc)
            ):
                try:
                    target_path = ""
                    try:
                        wf_args = json.loads(tool_call.raw_arguments or "{}")
                        target_path = wf_args.get("path", "")
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    if target_path:
                        self._prune_duplicate_writes(target_path)
                except Exception as prune_exc:
                    logger.debug("Prune after block failed: %s", prune_exc)

            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                error=error_msg,
                tool_call_id=tool_call.call_id,
            )
            if isinstance(exc, ToolPermissionError):
                self.stats.tool_calls_agreed -= 1
                self.stats.tool_calls_rejected += 1
            else:
                self.stats.tool_calls_failed += 1
            self._handle_tool_response(tool_call, error_msg, "failure", decision)

    def _silence_suppressed_failures(self, suppressed: list) -> None:
        """Add tool result messages for hallucinated tools without TUI events.

        Keeps message history well-formed (assistant tool_call → tool result)
        while hiding the error from the TUI to avoid confusing the user.
        """
        for failed in suppressed:
            error_msg = f"<{TOOL_ERROR_TAG}>{failed.tool_name}: {failed.error}</{TOOL_ERROR_TAG}>"
            self.messages.append(
                self.format_handler.create_failed_tool_response_message(failed, error_msg)
            )
            self.stats.tool_calls_failed += 1
            # Inject a [SYSTEM: ...] note so the model is more likely to break
            # out of the empty-response loop that often follows a suppressed
            # hallucinated-tool call.
            if "retrieve" in {t for t in self.tool_manager.available_tools}:
                note = (
                    f"'{failed.tool_name}' does not exist — do NOT call it again. "
                    "Call `retrieve(query='<terms>')` to search the project index, "
                    "or glob/grep/read_file for direct file access. Act NOW."
                )
            else:
                note = (
                    f"'{failed.tool_name}' does not exist — do NOT call it again. "
                    "Call glob, grep, or read_file NOW to make progress."
                )
            self._inject_system_note(note)

    async def _handle_tool_calls(
        self, resolved: ResolvedMessage
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent | ToolStreamEvent | AssistantEvent]:
        self._silence_suppressed_failures(resolved.suppressed_failures)
        async for event in self._emit_failed_tool_events(resolved.failed_calls):
            yield event
        for tool_call in resolved.tool_calls:
            yield ToolCallEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                args=tool_call.validated_args,
                tool_call_id=tool_call.call_id,
            )
            async for event in self._process_one_tool_call(tool_call):
                yield event

    def _handle_tool_response(
        self,
        tool_call: ResolvedToolCall,
        text: str,
        status: Literal["success", "failure", "skipped"],
        decision: ToolDecision | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.messages.append(
            LLMMessage.model_validate(
                self.format_handler.create_tool_response_message(tool_call, text)
            )
        )

        # Loop detection now drives TOKEN-LEVEL sampling bumps, not
        # advisory system notes (2026-04-13: confirmed 75/75 NOTICE
        # tool-result messages were ignored by Gemma 4, and nudge
        # system notes fired 4/4 with 8 identical calls continuing).
        # The tool-level ToolError escalation in read_file/bash (5x)
        # and search_replace (2x fail) is the hard stop for each
        # specific call; here we also bump sampling to make the NEXT
        # generated tool call likely to differ.
        try:
            repetition = self._check_tool_call_repetition()
            if repetition:
                self._loop_detected = True
                self._loop_signal = repetition
            else:
                self._loop_detected = False
                self._loop_signal = None
        except Exception as e:
            logger.debug("Loop detection check failed: %s", e)

        self.telemetry_client.send_tool_call_finished(
            tool_call=tool_call,
            agent_profile_name=self.agent_profile.name,
            status=status,
            decision=decision,
            result=result,
        )

        # === CURIOSITY: SURPRISE-ON-TOOL-RESULT ===
        # SOVEREIGN_PRD §5.7: when a tool result contradicts a confident
        # assertion the model just made (e.g., "All tests pass" right before
        # a Traceback), score the surprise and enqueue an EVIDENCE_CONFLICT
        # item for autonomous_review. Gated by DRYDOCK_CURIOSITY=1 (default).
        if status == "failure" and os.environ.get(
            "DRYDOCK_CURIOSITY", "1"
        ).strip().lower() not in ("0", "false", "no"):
            try:
                self._maybe_log_surprise(tool_call, text)
            except Exception as _e:
                logger.debug("surprise scoring skipped: %s", _e)

    def _maybe_log_surprise(self, tool_call: Any, tool_text: str) -> None:
        """Score the last assistant assertion against this tool result;
        enqueue an EVIDENCE_CONFLICT curiosity item if surprise is high."""
        try:
            from drydock.curiosity import (
                CuriosityItem, CuriosityKind, enqueue, score_surprise,
            )
            from drydock.curiosity.surprise import SURPRISE_THRESHOLD
        except Exception:
            return

        # Walk backward to find the most recent assistant CONTENT (not a
        # bare tool-call message). That's the assertion to compare against.
        prior_assertion = ""
        for msg in reversed(self.messages):
            if msg.role == Role.assistant and (msg.content or "").strip():
                prior_assertion = (msg.content or "").strip()
                break
        if not prior_assertion:
            return

        score = score_surprise(prior_assertion, tool_text, kind="tool_result")
        if score < SURPRISE_THRESHOLD:
            return

        tool_name = ""
        try:
            tool_name = getattr(tool_call.function, "name", "") or ""
        except Exception:
            pass

        enqueue(CuriosityItem(
            kind=CuriosityKind.EVIDENCE_CONFLICT,
            term=f"{tool_name} contradicted assistant claim",
            context=(
                f"Assistant said: {prior_assertion[:200]}\n"
                f"Tool {tool_name} returned: {tool_text[:200]}"
            ),
            source=f"session:{getattr(self, 'session_id', '?')}",
            suggested_action=(
                "Investigate whether this is a recurring model bias; "
                "consider a one-line AGENTS.md hint or sharpened prompt rule."
            ),
            confidence=float(score),
        ))

    # _build_repetition_nudge REMOVED (2026-04-13): advisory nudges had
    # 0% effect on Gemma 4 — fired 4 times while model made 8 identical
    # calls. Replaced by token-level sampling bumps (see agent_loop's
    # _loop_detected path) plus tool-level ToolError escalation in
    # read_file/bash/search_replace.

    def _prune_duplicate_writes(self, target_path: str) -> None:
        """Remove assistant-write_file / tool-result pairs for a looping path.

        Called after the hard-block fires on a write_file call. By that point
        the message history contains 3+ identical no-op write attempts to
        `target_path`, which bloats context and keeps nudging the model back
        toward the same action. Pruning them out gives the next turn a
        cleaner view.

        We keep:
          - the system prompt + first user message (they anchor the task)
          - the MOST RECENT write_file+result pair for this path (the one
            that just triggered the block — its error message is what the
            model needs to see)
          - everything unrelated to target_path

        We drop:
          - older write_file(path=target_path) assistant messages
          - their matching tool result messages

        This only prunes write_file calls where the path matches exactly and
        where the write_file is the ONLY tool call in that assistant message
        (to avoid removing unrelated calls in a multi-tool turn).
        """
        if len(self.messages) < 4:
            return

        # Find indices of all write_file assistant messages targeting this path
        target_indices: list[int] = []
        for i, msg in enumerate(self.messages):
            if msg.role != Role.assistant:
                continue
            tcs = msg.tool_calls or []
            if len(tcs) != 1:
                continue
            fn = tcs[0].function
            if not fn or fn.name != "write_file":
                continue
            try:
                args = json.loads(fn.arguments or "{}")
            except (json.JSONDecodeError, AttributeError):
                continue
            if args.get("path", "") == target_path:
                target_indices.append(i)

        if len(target_indices) < 2:
            return

        # Keep the MOST RECENT one; prune the rest (and their tool result)
        to_drop: set[int] = set()
        for idx in target_indices[:-1]:
            to_drop.add(idx)
            # The matching tool result is the next tool message
            for j in range(idx + 1, min(idx + 3, len(self.messages))):
                if self.messages[j].role == Role.tool:
                    to_drop.add(j)
                    break

        if not to_drop:
            return

        kept = [m for i, m in enumerate(self.messages) if i not in to_drop]
        logger.info(
            "Pruning %d message(s) from write loop on %s (history now %d → %d)",
            len(to_drop), target_path, len(self.messages), len(kept),
        )
        self.messages.reset(kept)

    def _proactive_prune_write_oscillation(self) -> None:
        """Prune duplicate writes BEFORE they push the session into a
        vLLM 400 Bad Request (context overflow).

        `_prune_duplicate_writes` above only fires after the hard-block
        trips on the Nth write. By that point the model has already
        written the file 4+ times, the context contains 4+ full copies
        of the file content, and vLLM has started returning 400s on
        every call.

        This method runs as part of _sanitize_message_ordering, BEFORE
        every LLM call. Any path with ≥3 `write_file` assistant-tool-
        call entries in history gets pruned down to the most recent 2.
        That leaves the latest attempt + one priored to compare against,
        without carrying an arbitrary number of historical copies.

        GitHub issue from 2026-04-21 user report: session wrote
        prepare.py 4× before hitting 15+ 400 errors and giving up.
        The existing `_prune_duplicate_writes` would have caught this,
        but only AFTER the hard-block fired (8+ identical calls).
        By 4× on a 600-line file we're already at ~75K tokens of
        duplicate content.
        """
        PROACTIVE_PRUNE_THRESHOLD = 3
        try:
            path_counts: dict[str, int] = {}
            for msg in self.messages:
                if msg.role != Role.assistant or not msg.tool_calls:
                    continue
                for tc in msg.tool_calls:
                    if not tc.function or tc.function.name != "write_file":
                        continue
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    target = args.get("file_path") or args.get("path") or ""
                    if not target:
                        continue
                    path_counts[target] = path_counts.get(target, 0) + 1
            for target_path, count in path_counts.items():
                if count >= PROACTIVE_PRUNE_THRESHOLD:
                    logger.info(
                        "Proactive prune: path %s has %d write_file "
                        "entries (threshold %d). Pruning older writes "
                        "before next LLM call to avoid context overflow.",
                        target_path, count, PROACTIVE_PRUNE_THRESHOLD,
                    )
                    self._prune_duplicate_writes(target_path)
        except Exception as e:
            logger.debug("Proactive write prune failed: %s", e)

    def _truncate_old_tool_results(self) -> None:
        """Shrink old verbose tool results before they bloat context.

        For local models like Gemma 4 the per-turn cost grows quadratically
        with context size, so a session with 30+ messages and a few large
        read_file results becomes unusable. This method:

        - Keeps the system prompt and the FIRST user message verbatim
          (instructions that should not be lost).
        - Keeps the last KEEP_RECENT tool results in full.
        - Truncates any older tool result whose content exceeds the
          per-result soft cap to a head + footer + size marker.

        Runs every turn but is a no-op when nothing exceeds the caps.
        Truncation is in-place and idempotent.
        """
        KEEP_RECENT = 4              # last N tool messages stay full
        SOFT_CAP_BYTES = 500         # tool result longer than this gets shrunk
        HEAD_BYTES = 200             # bytes kept from the head
        TAIL_BYTES = 60              # bytes kept from the tail

        if len(self.messages) < KEEP_RECENT + 4:
            return

        # Index of every tool message
        tool_idxs = [
            i for i, m in enumerate(self.messages) if m.role == Role.tool
        ]
        if len(tool_idxs) <= KEEP_RECENT:
            return

        # Truncate everything except the last KEEP_RECENT
        for idx in tool_idxs[:-KEEP_RECENT]:
            msg = self.messages[idx]
            content = str(msg.content or "")
            if len(content) <= SOFT_CAP_BYTES:
                continue
            if "[…truncated " in content and "bytes…]" in content:
                continue
            head = content[:HEAD_BYTES]
            tail = content[-TAIL_BYTES:]
            removed = len(content) - HEAD_BYTES - TAIL_BYTES
            msg.content = (
                f"{head}\n[…truncated {removed} bytes…]\n{tail}"
            )

        # Also truncate old ASSISTANT tool_call arguments (the REQUEST
        # side). Every write_file call carries the FULL file content in
        # function.arguments — this was the #1 context consumer (89K
        # tokens in the v2.6.102 session that rotted at prompt 23,
        # pushing total context to 131K = 100% of Gemma 4's limit).
        # Claude Code's microCompact targets BOTH tool results AND
        # tool_use blocks; our old code only shrunk results.
        # Keep the last KEEP_RECENT assistant-with-tools messages full;
        # truncate older ones' arguments to a small VALID JSON stub.
        # CRITICAL: arguments must remain valid JSON because vLLM's
        # tool-call parser re-parses them as JSON. The old code that
        # appended "\n[…truncated N bytes…]" injected raw newlines into
        # the JSON string and made vLLM 400 every request that hit the
        # truncated message — see issue #13 stress recurrence on
        # 2026-04-25 (each stress run accumulated dozens of 400s after
        # ~30 prompts as old write_file args got truncated this way).
        assistant_tc_idxs = [
            i for i, m in enumerate(self.messages)
            if m.role == Role.assistant and m.tool_calls
        ]
        if len(assistant_tc_idxs) > KEEP_RECENT:
            for idx in assistant_tc_idxs[:-KEEP_RECENT]:
                msg = self.messages[idx]
                if not msg.tool_calls:
                    continue
                for tc in msg.tool_calls:
                    if not tc.function or not tc.function.arguments:
                        continue
                    args = tc.function.arguments
                    if len(args) <= SOFT_CAP_BYTES:
                        continue
                    if '"_truncated"' in args:
                        continue
                    # Try to keep the most-useful field (path / file_path /
                    # command / cmd) plus a marker, rebuild as valid JSON.
                    stub: dict[str, Any] = {
                        "_truncated": True,
                        "_original_bytes": len(args),
                    }
                    try:
                        import json as _json
                        parsed = _json.loads(args)
                        if isinstance(parsed, dict):
                            for k in ("path", "file_path", "command",
                                      "cmd", "url", "file"):
                                if k in parsed and isinstance(parsed[k], str):
                                    v = parsed[k]
                                    stub[k] = (
                                        v if len(v) <= 200 else v[:200] + "…"
                                    )
                                    break
                    except Exception:
                        pass
                    tc.function.arguments = json.dumps(stub, ensure_ascii=True)

    def _sanitize_message_ordering(self) -> None:
        """Fix any role ordering violations before sending to vLLM/Mistral.

        vLLM/Mistral rejects:
        - 'user' messages immediately after 'tool' messages
        - 'assistant' as the last message (conflicts with add_generation_prompt)

        This runs as a safety net before every LLM call.
        """
        # Proactive context shrinkage runs first so the LLM call sees the
        # smaller payload.
        self._truncate_old_tool_results()
        self._proactive_prune_write_oscillation()

        if not self.messages:
            return

        # Fix 1: Merge any user messages that follow tool messages into the
        # nearest preceding tool message to avoid role ordering violations.
        cleaned: list[LLMMessage] = []
        for msg in self.messages:
            if (msg.role == Role.user
                    and cleaned
                    and cleaned[-1].role == Role.tool):
                # Merge into the preceding tool message
                cleaned[-1].content = (
                    (cleaned[-1].content or "") + f"\n\n[SYSTEM: {msg.content or ''}]"
                )
            else:
                cleaned.append(msg)
        if len(cleaned) != len(self.messages):
            self.messages.reset(cleaned)

        # Fix 2: Drop empty assistant messages (no content AND no tool_calls).
        # These violate the OpenAI schema and cause 400 errors on the next LLM
        # call. They arise when the model returns only thinking/reasoning tokens
        # (which get stripped) and stall-retry exhaustion leaves the empty msg
        # in history. An assistant message that follows a tool result must have
        # either content or tool_calls; if neither, drop it along with any
        # orphaned tool result messages that precede it (to keep role ordering
        # valid).
        cleaned2: list[LLMMessage] = []
        for msg in self.messages:
            if (msg.role == Role.assistant
                    and not (msg.content or "").strip()
                    and not msg.tool_calls):
                # Skip empty assistant — also drop any immediately preceding
                # tool result that would be truly orphaned (no matching
                # assistant.tool_calls entry in the preceding messages).
                while cleaned2 and cleaned2[-1].role == Role.tool:
                    preceding_tool = cleaned2[-1]
                    tcid = getattr(preceding_tool, "tool_call_id", None)
                    if tcid and any(
                        m.role == Role.assistant
                        and m.tool_calls
                        and any(tc.id == tcid for tc in m.tool_calls)
                        for m in cleaned2[:-1]
                    ):
                        break  # tool result has a valid match; keep it
                    cleaned2.pop()
            else:
                cleaned2.append(msg)
        if len(cleaned2) != len(self.messages):
            self.messages.reset(cleaned2)

        # Fix 3: If last message is assistant, add a user "Continue." prompt.
        # The auto-Continue exists so Gemma 4 keeps executing multi-step plans
        # without stopping prematurely at an intermediate text response. For
        # stress runs against prompts that don't need tool calls (pure
        # doc-writing tasks), it loops forever — model writes the answer,
        # "Continue." is appended, model regenerates the same answer, repeat.
        # Gate on DRYDOCK_AUTO_CONTINUE_DISABLE so stress harnesses can opt out
        # without changing default behavior.
        if (self.messages and self.messages[-1].role == Role.assistant
                and not os.environ.get("DRYDOCK_AUTO_CONTINUE_DISABLE")):
            self.messages.append(LLMMessage(role=Role.user, content="Continue."))

    def _choose_thinking_level(self, active_model: Any) -> str:
        """Adapt thinking level based on conversation state.

        Thinking is expensive for Gemma 4 (~70 tok/s).  Using "high" on
        every turn causes 30-120s hangs between file writes.  Instead:

        HIGH — first response, user messages, planning/complex decisions
        LOW  — after tool errors (debug, but keep it brief)
        OFF  — after successful tool results, system notes (just act)
        """
        base = active_model.thinking
        if base in ("off", ""):
            return base  # thinking disabled entirely — respect that

        # Only adapt for local models where thinking is slow
        if "gemma" not in active_model.name.lower():
            return base

        # Early conversation (first few turns): full thinking for planning
        # The model needs to understand the task and make a plan.
        if len(self.messages) <= 4:
            return base

        # Look at the last message to decide
        last = self.messages[-1] if self.messages else None
        if last is None:
            return base

        if last.role == Role.user:
            content = str(last.content or "")
            # System note / loop nudge / project-context injection → act
            # immediately. Empirically: Gemma 4 on bare prompts post-build
            # with thinking=high spends ~30s thinking and then returns an
            # empty response anyway. Keeping thinking=off makes per-prompt
            # latency ~3x faster and produces quick text/tool responses
            # that let the harness keep moving. v118 stress reached step
            # 318 with this; v121 (which restricted to startswith) only
            # reached step 23 because each silent prompt cost 30s+.
            if "[SYSTEM" in content:
                return "off"
            # Real user message → full thinking (they're asking something new)
            return base

        if last.role == Role.tool:
            content = str(last.content or "")
            # Tool error → think about the fix (but not too long)
            if "<tool_error>" in content or "error" in content.lower()[:100]:
                return "low"
            # read_file result → might need to reason about the code
            if "content:" in content[:50] and len(content) > 500:
                return "low"
            # Successful write/bash → just keep going
            return "off"

        # Default: configured level
        return base

    async def _chat(self, max_tokens: int | None = None) -> LLMChunk:
        _t0 = time.perf_counter()
        self._sanitize_message_ordering()
        _t1 = time.perf_counter()

        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)

        available_tools = self.format_handler.get_available_tools(self.tool_manager)
        tool_choice = self.format_handler.get_tool_choice()

        # Loop-break when FORCE_STOP detected. Two-tier:
        #   1. If _hot_tool_path is set (specific tool+path dominates the
        #      recent window), REMOVE that tool from available_tools for
        #      this turn. Model must diversify — can still use other tools.
        #      This is surgical: the model can read, bash, SR, etc., just
        #      can't call the over-used tool on the over-used path.
        #   2. Otherwise (generic FORCE_STOP), fall back to tool_choice=none
        #      so model emits text. Last resort.
        # The hot-path flag is consumed (cleared) here so it's a one-turn
        # mute. If the model goes back to looping next turn, we'll re-detect
        # and re-mute.
        if getattr(self, "_hle_force_text_only", False):
            tool_choice = "none"
            self._hle_force_text_only = False
            logger.info("[TOOL-STOP] model ignored stop note — forcing tool_choice=none for 1 turn")
        elif getattr(self, "_loop_detected", False) and getattr(self, "_loop_signal", "") == "FORCE_STOP":
            hot = getattr(self, "_hot_tool_path", None)
            if hot and hot[0] and available_tools:
                hot_tool_name, hot_path = hot
                before = len(available_tools)
                available_tools = [
                    t for t in available_tools
                    if getattr(t.function, "name", None) != hot_tool_name
                ]
                after = len(available_tools)
                if before != after:
                    logger.info(
                        "[LOOP-BREAK] FORCE_STOP hot=(%s, %s) — "
                        "removed '%s' from available_tools for 1 turn "
                        "(%d → %d tools). Model must diversify.",
                        hot_tool_name, hot_path[:50], hot_tool_name, before, after,
                    )
            else:
                # No specific tool+path hot-combo — fall back to text-only.
                tool_choice = "none"
                logger.info(
                    "[LOOP-BREAK] FORCE_STOP (no hot-combo) → tool_choice=none"
                )
            # Consume ALL loop flags — one-turn action. Must reset
            # _loop_detected too, otherwise the flag persists across
            # turns (because _check_tool_call_repetition only updates
            # it on tool-result handling, which skips when the model
            # emits empty responses — so tool_choice=none would stay
            # sticky forever, leading to infinite empty-reply stalls).
            self._hot_tool_path = None
            self._loop_detected = False
            self._loop_signal = None
        _t2 = time.perf_counter()

        n_msgs = len(self.messages)
        n_tools = len(available_tools) if available_tools else 0
        logger.info(
            "[TIMING] _chat start: sanitize=%.2fs prep=%.2fs msgs=%d tools=%d",
            _t1 - _t0, _t2 - _t1, n_msgs, n_tools,
        )

        # Adaptive thinking: reduce thinking on routine turns
        original_thinking = active_model.thinking
        active_model.thinking = self._choose_thinking_level(active_model)
        if active_model.thinking != original_thinking:
            logger.info(
                "[THINKING] %s → %s (last msg role=%s)",
                original_thinking, active_model.thinking,
                self.messages[-1].role if self.messages else "?",
            )

        try:
            start_time = time.perf_counter()
            temp = active_model.temperature
            extra_sampling: dict | None = None

            # Per-model `extra_params` (top_k, top_p, frequency_penalty,
            # max_tokens, etc.) declared in config.toml flow through
            # extra_sampling. This is the seam llama.cpp users need to
            # pass top_k=40, top_p=0.95, frequency_penalty=1.1 per the
            # Gemma 4 loop-fix recipe.
            cfg_extra = getattr(active_model, "extra_params", None) or {}
            if cfg_extra:
                extra_sampling = dict(cfg_extra)

            # Token-level loop-breaker: when repetition is detected, bump
            # temperature and add frequency_penalty + a fresh seed so the
            # model's next completion is mechanically likely to diverge.
            # Mistral/OpenAI-compat backends pass these straight through
            # to vLLM's SamplingParams. These OVERRIDE config.extra_params
            # for the duration of the loop-break.
            if getattr(self, "_loop_detected", False):
                signal = getattr(self, "_loop_signal", "") or ""
                # Heavier bump if we've already hit the FORCE_STOP signal
                # (=8 repeats) vs a WARNING (=3-5 repeats).
                heavy = signal == "FORCE_STOP"
                temp = min(1.0, temp + (0.5 if heavy else 0.3))
                # Merge loop-breaker overrides on top of any cfg_extra so
                # config-declared sampling params survive when a loop is
                # NOT detected, and get overridden when one IS detected.
                if extra_sampling is None:
                    extra_sampling = {}
                extra_sampling.update({
                    "frequency_penalty": 0.7 if heavy else 0.4,
                    "presence_penalty": 0.3,
                    "seed": int(time.time() * 1000) & 0x7FFFFFFF,
                })
                logger.info(
                    "[LOOP-BREAK] %s → temp %.2f, freq_pen %.2f, seed %d",
                    signal, temp, extra_sampling["frequency_penalty"],
                    extra_sampling["seed"],
                )
                # ALWAYS clear the loop flags after consuming them — for
                # both FORCE_STOP and WARNING signals. _check_tool_call_
                # repetition only updates these when handling a tool
                # result; if the model emits text-only (no tool call) the
                # check never runs and the flag stays set forever, baking
                # frequency_penalty=0.4 into every subsequent generation.
                # That suppresses repeated tokens INCLUDING SPACE — the
                # user-reported "no spaces in TUI text" was caused here.
                self._loop_detected = False
                self._loop_signal = None

            # Deep Noir steering hook — env-gated, log-only by default.
            # No-op unless DRYDOCK_STEERING_MODES is set; never raises.
            steering_logit_bias: dict[int, float] | None = None
            try:
                from drydock.core.steering_hook import (
                    decide_for_request,
                    logit_bias_for_request,
                )
                steering_decision = decide_for_request(active_model.name)
                if steering_decision is not None:
                    logger.info("[STEERING] %s", steering_decision.summary())
                    if steering_decision.applier_kind == "logit_bias":
                        bias = logit_bias_for_request(active_model.name)
                        if bias:
                            steering_logit_bias = bias
                            logger.info(
                                "[STEERING] logit_bias entries: %d",
                                len(bias),
                            )
            except Exception as _e:  # defense in depth
                logger.debug("steering hook bypassed: %s", _e)

            complete_kwargs = dict(
                model=active_model,
                messages=self.messages,
                temperature=temp,
                tools=available_tools,
                tool_choice=tool_choice,
                extra_headers=self._get_extra_headers(provider),
                max_tokens=max_tokens,
                metadata=self.entrypoint_metadata.model_dump()
                if self.entrypoint_metadata
                else None,
            )
            if extra_sampling:
                complete_kwargs["extra_sampling"] = extra_sampling
            if steering_logit_bias:
                # Merge into extra_sampling so vLLM/Mistral backends pick it up
                # via SamplingParams. Backends that don't understand logit_bias
                # will TypeError below and we'll retry without extra_sampling
                # (keeping inference behavior intact).
                merged = dict(extra_sampling) if extra_sampling else {}
                merged["logit_bias"] = steering_logit_bias
                complete_kwargs["extra_sampling"] = merged
            try:
                result = await self.backend.complete(**complete_kwargs)
            except TypeError:
                # Older backend that doesn't accept extra_sampling
                complete_kwargs.pop("extra_sampling", None)
                result = await self.backend.complete(**complete_kwargs)
            end_time = time.perf_counter()

            logger.info(
                "[TIMING] backend.complete returned in %.2fs (prompt=%s completion=%s)",
                end_time - start_time,
                result.usage.prompt_tokens if result.usage else "?",
                result.usage.completion_tokens if result.usage else "?",
            )
            if result.usage is None:
                raise AgentLoopLLMResponseError(
                    "Usage data missing in non-streaming completion response"
                )
            self._update_stats(usage=result.usage, time_seconds=end_time - start_time)

            processed_message = self.format_handler.process_api_response_message(
                result.message
            )
            self.messages.append(processed_message)
            return LLMChunk(message=processed_message, usage=result.usage)

        except Exception as e:
            if _should_raise_rate_limit_error(e):
                raise RateLimitError(provider.name, active_model.name) from e

            raise RuntimeError(
                f"API error from {provider.name} (model: {active_model.name}): {e}"
            ) from e
        finally:
            # Restore thinking level so the config stays clean
            active_model.thinking = original_thinking

    async def _chat_streaming(
        self, max_tokens: int | None = None
    ) -> AsyncGenerator[LLMChunk]:
        self._sanitize_message_ordering()

        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)

        available_tools = self.format_handler.get_available_tools(self.tool_manager)
        tool_choice = self.format_handler.get_tool_choice()
        try:
            start_time = time.perf_counter()
            usage = LLMUsage()
            chunk_agg = LLMChunk(message=LLMMessage(role=Role.assistant))
            # Use temperature override if set (loop detection bumps it)
            temp = active_model.temperature
            async for chunk in self.backend.complete_streaming(
                model=active_model,
                messages=self.messages,
                temperature=temp,
                tools=available_tools,
                tool_choice=tool_choice,
                extra_headers=self._get_extra_headers(provider),
                max_tokens=max_tokens,
                metadata=self.entrypoint_metadata.model_dump()
                if self.entrypoint_metadata
                else None,
            ):
                processed_message = self.format_handler.process_api_response_message(
                    chunk.message
                )
                processed_chunk = LLMChunk(message=processed_message, usage=chunk.usage)
                chunk_agg += processed_chunk
                usage += chunk.usage or LLMUsage()
                yield processed_chunk
            end_time = time.perf_counter()

            if chunk_agg.usage is None:
                raise AgentLoopLLMResponseError(
                    "Usage data missing in final chunk of streamed completion"
                )
            self._update_stats(usage=usage, time_seconds=end_time - start_time)

            # DEBUG: dump accumulated message for diagnosis
            msg = chunk_agg.message
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function:
                        logger.warning(
                            "ACCUMULATED TOOL CALL: name=%s args_len=%d args_first100=%s",
                            tc.function.name,
                            len(tc.function.arguments or ""),
                            (tc.function.arguments or "")[:100],
                        )

            self.messages.append(chunk_agg.message)

        except Exception as e:
            if _should_raise_rate_limit_error(e):
                raise RateLimitError(provider.name, active_model.name) from e

            raise RuntimeError(
                f"API error from {provider.name} (model: {active_model.name}): {e}"
            ) from e

    def _update_stats(self, usage: LLMUsage, time_seconds: float) -> None:
        self.stats.last_turn_duration = time_seconds
        self.stats.last_turn_prompt_tokens = usage.prompt_tokens
        self.stats.last_turn_completion_tokens = usage.completion_tokens
        self.stats.session_prompt_tokens += usage.prompt_tokens
        self.stats.session_completion_tokens += usage.completion_tokens
        self.stats.context_tokens = usage.prompt_tokens + usage.completion_tokens
        if time_seconds > 0 and usage.completion_tokens > 0:
            self.stats.tokens_per_second = usage.completion_tokens / time_seconds

    async def _should_execute_tool(
        self, tool: BaseTool, args: BaseModel, tool_call_id: str
    ) -> ToolDecision:
        if self.auto_approve:
            return ToolDecision(
                verdict=ToolExecutionResponse.EXECUTE,
                approval_type=ToolPermission.ALWAYS,
            )

        tool_name = tool.get_name()
        effective = (
            tool.resolve_permission(args)
            or self.tool_manager.get_tool_config(tool_name).permission
        )

        match effective:
            case ToolPermission.ALWAYS:
                return ToolDecision(
                    verdict=ToolExecutionResponse.EXECUTE,
                    approval_type=ToolPermission.ALWAYS,
                )
            case ToolPermission.NEVER:
                return ToolDecision(
                    verdict=ToolExecutionResponse.SKIP,
                    approval_type=ToolPermission.NEVER,
                    feedback=f"Tool '{tool_name}' is permanently disabled",
                )
            case _:
                return await self._ask_approval(tool_name, args, tool_call_id)

    async def _ask_approval(
        self, tool_name: str, args: BaseModel, tool_call_id: str
    ) -> ToolDecision:
        if not self.approval_callback:
            return ToolDecision(
                verdict=ToolExecutionResponse.SKIP,
                approval_type=ToolPermission.ASK,
                feedback="Tool execution not permitted.",
            )
        if asyncio.iscoroutinefunction(self.approval_callback):
            async_callback = cast(AsyncApprovalCallback, self.approval_callback)
            response, feedback = await async_callback(tool_name, args, tool_call_id)
        else:
            sync_callback = cast(SyncApprovalCallback, self.approval_callback)
            response, feedback = sync_callback(tool_name, args, tool_call_id)

        match response:
            case ApprovalResponse.YES:
                return ToolDecision(
                    verdict=ToolExecutionResponse.EXECUTE,
                    approval_type=ToolPermission.ASK,
                    feedback=feedback,
                )
            case ApprovalResponse.NO:
                return ToolDecision(
                    verdict=ToolExecutionResponse.SKIP,
                    approval_type=ToolPermission.ASK,
                    feedback=feedback,
                )

    def _list_created_files(self) -> list[str]:
        """List files the model has successfully created/written in this session."""
        files = set()
        for msg in self.messages:
            if msg.role == Role.tool and msg.content:
                content = str(msg.content)
                # Look for write_file success indicators
                if "bytes_written" in content or "Created" in content or "Overwritten" in content:
                    # Extract path from tool result
                    import re
                    path_match = re.search(r'"path":\s*"([^"]+)"', content)
                    if path_match:
                        files.add(Path(path_match.group(1)).name)
                    else:
                        # Try extracting from "Created X" or "Overwritten X"
                        name_match = re.search(r'(?:Created|Overwritten)\s+(\S+)', content)
                        if name_match:
                            files.add(name_match.group(1))
        return sorted(files)

    def _detect_stuck_file(self) -> str | None:
        """Detect if the model is writing the same file repeatedly."""
        write_paths: list[str] = []
        for msg in reversed(self.messages[-20:]):
            if msg.role == Role.assistant and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function and tc.function.name in ("write_file", "search_replace"):
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            path = args.get("path", args.get("file_path", ""))
                            if path:
                                write_paths.append(path)
                        except (json.JSONDecodeError, AttributeError):
                            pass
        if write_paths:
            from collections import Counter
            path_counts = Counter(write_paths)
            most_common_path, count = path_counts.most_common(1)[0]
            if count >= 3:
                return most_common_path
        return None

    def _record_failed_approach(self) -> None:
        """Record a one-line summary of the most recent failed approach.

        Survives pruning and compaction so the model doesn't retry
        the same strategy after context cleanup.
        """
        if not hasattr(self, '_failed_approaches'):
            self._failed_approaches = []

        # Extract the last tool call and its result
        last_tool_name = ""
        last_tool_args = ""
        last_result = ""
        for msg in reversed(self.messages[-6:]):
            if msg.role == Role.tool and not last_result:
                result = str(msg.content or "")[:100]
                if "error" in result.lower() or "not found" in result.lower():
                    last_result = result[:80]
            elif msg.role == Role.assistant and msg.tool_calls and not last_tool_name:
                for tc in msg.tool_calls:
                    if tc.function:
                        last_tool_name = tc.function.name or ""
                        args = tc.function.arguments or ""
                        # Extract file path if present
                        try:
                            parsed = json.loads(args)
                            last_tool_args = parsed.get("file_path", parsed.get("path", parsed.get("command", "")))[:60]
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            last_tool_args = args[:40]
                        break

        if last_tool_name:
            summary = f"{last_tool_name}({last_tool_args})"
            if last_result:
                summary += f" → {last_result}"
            # Don't add duplicates
            if not self._failed_approaches or self._failed_approaches[-1] != summary:
                self._failed_approaches.append(summary)
                # Keep only last 10
                self._failed_approaches = self._failed_approaches[-10:]

    def _build_retrospection(self) -> str | None:
        """Build a retrospection summary of recent tool calls.

        Instead of hardcoded nudges, show the model what it's been doing
        and let it decide on a different approach. The model is better at
        self-correcting when it can see its own pattern of behavior.
        """
        # Collect last 8 tool calls with their results (truncated)
        recent: list[str] = []
        count = 0
        for msg in reversed(self.messages):
            if count >= 8:
                break
            if msg.role == Role.assistant and msg.tool_calls:
                for tc in reversed(msg.tool_calls or []):
                    if tc.function:
                        name = tc.function.name or "?"
                        args_str = tc.function.arguments or ""
                        # Truncate args for readability
                        if len(args_str) > 120:
                            args_str = args_str[:120] + "..."
                        recent.append(f"  {count+1}. {name}({args_str})")
                        count += 1
                        if count >= 8:
                            break
            elif msg.role == Role.tool and recent:
                # Add result summary to last entry
                result = str(msg.content or "")[:150]
                if "error" in result.lower() or "Error" in result:
                    recent[-1] += f" → ERROR"
                elif "not found" in result.lower():
                    recent[-1] += f" → NOT FOUND"
                else:
                    recent[-1] += f" → ok"

        if len(recent) < 3:
            return None

        recent.reverse()
        summary = "\n".join(recent)

        return (
            f"RETROSPECTION — Your last {len(recent)} tool calls:\n"
            f"{summary}\n\n"
            f"You are repeating a pattern that is not making progress. "
            f"Review the sequence above. What went wrong? What should you do differently? "
            f"Choose a different approach on your own."
        )

    def _inject_system_note(self, text: str, replace_last_tool: bool = False) -> None:
        """Safely inject a system note into conversation without breaking message ordering.

        vLLM/Mistral rejects:
        - 'user' messages after 'tool' messages
        - 'assistant' messages before another LLM turn (add_generation_prompt conflict)

        Always appends to the nearest tool result. If none exists, appends to the
        last non-assistant message. Never creates a new message.
        """
        # First try: append to last tool message
        for msg in reversed(self.messages):
            if msg.role == Role.tool:
                if replace_last_tool:
                    msg.content = f"[SYSTEM: {text}]"
                else:
                    content = msg.content or ""
                    # Cap at 2 accumulated system notes per message; replace the
                    # last one when the limit is exceeded so repeated admiral
                    # interventions don't unboundedly bloat the context.
                    _SYS_PREFIX = "\n\n[SYSTEM: "
                    if content.count(_SYS_PREFIX) >= 2:
                        last_idx = content.rfind(_SYS_PREFIX)
                        msg.content = content[:last_idx] + _SYS_PREFIX + text + "]"
                    else:
                        msg.content = content + _SYS_PREFIX + text + "]"
                return

        # Second try: append to last user message
        for msg in reversed(self.messages):
            if msg.role == Role.user:
                msg.content = (msg.content or "") + f"\n\n[SYSTEM: {text}]"
                return

        # Last resort: silently drop — better than crashing the conversation
        logger.warning("Could not inject system note (no tool/user message found): %s", text[:100])

    def _check_tool_call_repetition(self) -> str | None:
        """Check if recent tool calls are repeating. Returns 'FORCE_STOP', 'WARNING', or None."""

        # Early check: hallucinated tool called in last 40 messages.
        # The stall nudge already tells the model "this tool doesn't exist" but
        # Gemma 4 / Opus ignore it and loop.  Force text-only for one turn so
        # the model must write content instead of calling the ghost tool again.
        # Threshold=1: sessions typically only make 1-2 ghost calls before
        # timing out, so the old threshold=3 never triggered in practice.
        if hasattr(self, "tool_manager") and self.tool_manager:
            _avail = self.tool_manager.available_tools
            _hall_names: list[str] = []
            for _hm in reversed(self.messages[-40:]):
                if _hm.role == Role.assistant and _hm.tool_calls:
                    for _htc in _hm.tool_calls:
                        if _htc.function and _htc.function.name:
                            if _htc.function.name not in _avail:
                                _hall_names.append(_htc.function.name)
                if len(_hall_names) >= 10:
                    break
            if _hall_names:
                from collections import Counter as _HCtr
                _top_h, _top_h_cnt = _HCtr(_hall_names).most_common(1)[0]
                if _top_h_cnt >= 1:
                    # _hot_tool_path=None → FORCE_STOP handler sets tool_choice=none
                    self._hot_tool_path = None
                    return "FORCE_STOP"

        # Early check: search_replace with the same file + old_string twice
        # in a row.  This is the #1 user-pain loop — the model retries an
        # edit that already succeeded or that keeps failing with the same
        # "not found" error.  Nudge after just 2 identical attempts.
        recent_sr: list[str] = []
        recent_sr_files: list[str] = []
        for msg in reversed(self.messages[-30:]):
            if msg.role == Role.assistant and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function and tc.function.name == "search_replace":
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            # Build a key from file_path + old_string (content block)
                            key = f"{args.get('file_path', '')}:{args.get('content', '')}"
                            recent_sr.append(key)
                            recent_sr_files.append(args.get("file_path", ""))
                        except (json.JSONDecodeError, AttributeError):
                            pass
                if len(recent_sr) >= 6:
                    break
        if len(recent_sr) >= 2 and recent_sr[0] == recent_sr[1]:
            return "WARNING|search_replace"
        # Detect when 5+ search_replace calls target the same file with
        # varying search text (model adapts after HARD-STOP but still
        # cannot find the right text). The per-file fail counter in
        # search_replace.py escalates at count 3 — this adds a
        # loop-detection layer that fires FORCE_STOP when the same file
        # dominates 5 of the last 6 search_replace calls.
        if len(recent_sr_files) >= 5:
            from collections import Counter as _SRCounter
            _sr_counts = _SRCounter(f for f in recent_sr_files if f)
            if _sr_counts:
                _top_sr_file, _top_sr_count = _sr_counts.most_common(1)[0]
                if _top_sr_count >= 5:
                    self._hot_tool_path = ("search_replace", _top_sr_file)
                    return "FORCE_STOP"

        # Early check: write_file with _truncated args twice in a row for the
        # same path.  format.py already embeds the file content in the error,
        # but Gemma 4 ignores the advisory and retries identically.  Mute
        # write_file for 1 turn so the model must use read_file or
        # search_replace instead — same pattern as the search_replace check.
        recent_wf_truncated: list[str] = []
        for msg in reversed(self.messages[-20:]):
            if msg.role == Role.assistant and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function and tc.function.name == "write_file":
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            if args.get("_truncated"):
                                p = (args.get("file_path") or args.get("path") or "")
                                recent_wf_truncated.append(p)
                        except (json.JSONDecodeError, AttributeError):
                            pass
                if len(recent_wf_truncated) >= 2:
                    break
        if (len(recent_wf_truncated) >= 2
                and recent_wf_truncated[0] == recent_wf_truncated[1]):
            self._hot_tool_path = ("write_file", recent_wf_truncated[0])
            return "FORCE_STOP"

        # Early check: same bash command 5+ times across last 20 tool calls.
        # Catches alternating bash/read_file exploration loops where neither
        # the consecutive-N check nor the 9/12 path-dominance check fires
        # (because bash and read_file alternate, keeping bash below 9/12).
        _bash_cmds: list[str] = []
        _total_tc = 0
        for _msg in reversed(self.messages[-40:]):
            if _msg.role == Role.assistant and _msg.tool_calls:
                for _tc in _msg.tool_calls:
                    _total_tc += 1
                    if _tc.function and _tc.function.name == "bash":
                        try:
                            _a = json.loads(_tc.function.arguments or "{}")
                            _cmd = _a.get("command", "")
                            if _cmd:
                                _bash_cmds.append(_cmd)
                        except (json.JSONDecodeError, AttributeError):
                            pass
            if _total_tc >= 20:
                break
        # Consecutive check: 3+ identical bash commands in a row → FORCE_STOP.
        # The admiral nudges at 3 consecutive identical calls; without a hard
        # stop here the model runs 2–4 more before the 5-total check below fires.
        # (_bash_cmds is newest-first, so [0][1][2] = last 3 in order)
        if len(_bash_cmds) >= 3 and _bash_cmds[0] == _bash_cmds[1] == _bash_cmds[2]:
            self._hot_tool_path = ("bash", _bash_cmds[0])
            return "FORCE_STOP"
        if len(_bash_cmds) >= 5:
            from collections import Counter as _Counter
            _cmd_counts = _Counter(_bash_cmds)
            _top_cmd, _top_count = _cmd_counts.most_common(1)[0]
            if _top_count >= 5:
                self._hot_tool_path = ("bash", _top_cmd)
                return "FORCE_STOP"

        sigs: list[str] = []
        tool_names: list[str] = []
        lookback = 0
        for msg in reversed(self.messages):
            lookback += 1
            if lookback > 50:
                break
            if msg.role == Role.assistant and msg.tool_calls:
                call_parts = []
                for tc in msg.tool_calls:
                    if tc.function:
                        call_parts.append(
                            f"{tc.function.name}:{tc.function.arguments}"
                        )
                        tool_names.append(tc.function.name or "")
                sig = hashlib.sha256(
                    "|".join(sorted(call_parts)).encode()
                ).hexdigest()[:16]
                sigs.append(sig)
                if len(sigs) >= 15:
                    break

        sigs.reverse()
        tool_names.reverse()

        # Check 0: Same call + empty-pattern result 3+ times in a row.
        # Lower threshold than Check 1 (8) or the WARNING path (4) for
        # the specific case where the tool keeps saying "nothing's
        # here" — more calls literally cannot change state. GitHub #10:
        # model called todo_list 4× getting "Retrieved 0 todos"; the
        # existing Check 1 wouldn't fire until 8, leaving the user
        # staring at a blob of empty calls for ~half a minute.
        #
        # We only fire when the result LOOKS empty (total_count: 0,
        # "no todos", "no tasks", "no results", or entirely blank).
        # Same call with a non-empty result (e.g., model re-reading a
        # file whose content hasn't changed) is left to Check 1/WARNING
        # — those cases often have legitimate ambiguity.
        def _looks_empty(c: str) -> bool:
            s = (c or "").lower().strip()
            if not s:
                return True
            for p in ('"total_count": 0', '"total_count":0',
                      'total_count: 0', 'retrieved 0 todos',
                      'no todos', '0 tasks', 'no tasks',
                      'no results', 'no matches', '0 matches',
                      'no relevant information found'):
                if p in s:
                    return True
            return False
        if (len(sigs) >= EMPTY_RESULT_THRESHOLD
                and all(s == sigs[-1] for s in sigs[-EMPTY_RESULT_THRESHOLD:])):
            recent_results: list[str] = []
            for msg in reversed(self.messages):
                if msg.role == Role.tool:
                    recent_results.append(str(msg.content or ""))
                    if len(recent_results) >= EMPTY_RESULT_THRESHOLD:
                        break
            if (len(recent_results) >= EMPTY_RESULT_THRESHOLD
                    and all(_looks_empty(r) for r in recent_results)):
                return "FORCE_STOP"

        # Check 1: Exact same tool call repeated (same name + same args)
        last_tool = tool_names[-1] if tool_names else ""
        if (
            len(sigs) >= REPEAT_FORCE_STOP_THRESHOLD
            and all(s == sigs[-1] for s in sigs[-REPEAT_FORCE_STOP_THRESHOLD:])
        ):
            return "FORCE_STOP"

        # Check 1a: Same TOOL NAME repeated consecutively, regardless of args.
        # Catches the "write_file with missing/corrupted args 36 times in a row"
        # pathology where each sig differs but the model is clearly stuck.
        # Threshold is lower for exploration/indexing tools (ralph_repo_index,
        # read_file, glob, grep) that should never need 4+ consecutive calls —
        # each call after the first is a stall-recovery loop, not progress.
        # Write/shell tools keep the higher threshold (8) since they legitimately
        # appear many times in sequence when building multi-file projects.
        # Record a hot-combo on the stuck tool with an empty-path marker
        # so the per-tool mute in _chat will remove it for 1 turn.
        # Uniform threshold=8. The autonomous_review fix that lowered this to
        # 4 for exploration tools (grep/read_file/glob) addressed a misattributed
        # `harness:thinking_stall` signal — that pattern is about post-thinking
        # empty messages, handled by the empty-message nudge elsewhere — and the
        # lowered threshold broke loop_detection regression tests (4 different
        # greps or read_files is legitimate investigation, not a stuck loop).
        if len(tool_names) >= 8:
            last8 = tool_names[-8:]
            if all(n == last8[-1] for n in last8):
                self._hot_tool_path = (last8[-1], "<stuck>")
                return "FORCE_STOP"

        # Check 1c: High recent-error fraction. If ≥6 of last 10 tool
        # calls returned errors AND they're the same tool, that's a
        # stuck-in-error-retry loop. FORCE_STOP + mute the tool.
        if len(tool_names) >= 10:
            recent_names = tool_names[-10:]
            last_name = recent_names[-1]
            same_name_count = sum(1 for n in recent_names if n == last_name)
            if same_name_count >= 8:
                # Count tool_error results in last ~20 messages
                recent_errors = 0
                for msg in list(reversed(self.messages))[:30]:
                    if msg.role == Role.tool:
                        c = str(msg.content or "")
                        if "<tool_error>" in c or "Invalid arguments" in c:
                            recent_errors += 1
                    if recent_errors >= 6:
                        break
                if recent_errors >= 6:
                    self._hot_tool_path = (last_name, "<error-storm>")
                    return "FORCE_STOP"

        # Check 1b: Path-dominance oscillation. The model is stuck on a
        # single file — writing, rewriting, SR-patching, reading — even
        # though each call's signature differs enough to dodge Check 1.
        # If ≥9 of the last 12 tool calls touch the SAME path, record
        # the (tool, path) hot-combo for the NEXT turn to act on.
        #
        # Tolerance: normal feature-addition work spreads writes across
        # many prompts (+3 writes per prompt over 100+ tool calls in
        # the stress run), so 9-of-12 is a much tighter cluster than
        # legitimate progress.
        if len(sigs) >= 12:
            # Collect recent (tool_name, path_or_command) pairs for
            # hot-combo detection. Include `command` as a path-like key
            # so bash loops (same command run 8+ times) are caught too.
            path_tool_pairs: list[tuple[str, str]] = []
            paths_only: list[str] = []
            for msg in list(reversed(self.messages))[:40]:
                if msg.role == Role.assistant and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if not tc.function:
                            continue
                        try:
                            a = json.loads(tc.function.arguments or "{}")
                        except (json.JSONDecodeError, AttributeError):
                            continue
                        # Path-like identity key: file path OR command.
                        # For bash, the "path" is the command string.
                        p = (a.get("file_path") or a.get("path")
                             or a.get("command") or "")
                        if p:
                            paths_only.append(str(p)[:200])
                            path_tool_pairs.append((tc.function.name or "", str(p)[:200]))
                        if len(paths_only) >= 12:
                            break
                    if len(paths_only) >= 12:
                        break
            last_12_paths = paths_only[:12]
            last_12_pairs = path_tool_pairs[:12]
            if len(last_12_paths) >= 12:
                most_path = max(set(last_12_paths), key=last_12_paths.count)
                if last_12_paths.count(most_path) >= 9:
                    # Record the dominating (tool, path) pair with the
                    # highest count so the agent can mute that specific
                    # tool on that path next turn.
                    from collections import Counter
                    pair_counts = Counter(last_12_pairs)
                    top_pair, top_count = pair_counts.most_common(1)[0]
                    self._hot_tool_path = top_pair if top_count >= 5 else (None, most_path)
                    return "FORCE_STOP"
        # Reset hot-path when no path-dominance detected
        if not getattr(self, "_hot_tool_path", None) or True:
            pass  # hot path set only when triggered; consumed in _chat

        if (
            len(sigs) >= REPEAT_WARNING_THRESHOLD
            and all(s == sigs[-1] for s in sigs[-REPEAT_WARNING_THRESHOLD:])
        ):
            return f"WARNING|{last_tool}"

        # Check 2: Same tool called N+ times consecutively with different args
        # Lower thresholds catch loops where the model uses the same tool
        # with slightly different args (e.g., grep with different paths).
        # Limits are env-overridable via DRYDOCK_ADMIRAL_SAME_TOOL_NAME_REPEAT_LIMIT_*.
        if last_tool in ("bash", "run_command"):
            same_tool_limit = SAME_TOOL_NAME_REPEAT_LIMIT_BASH
        elif last_tool in ("grep", "read_file"):
            # investigation tools need some room
            same_tool_limit = SAME_TOOL_NAME_REPEAT_LIMIT_READ
        else:
            same_tool_limit = SAME_TOOL_NAME_REPEAT_LIMIT_BASH
        if (
            len(tool_names) >= same_tool_limit
            and all(n == tool_names[-1] for n in tool_names[-same_tool_limit:])
        ):
            return f"WARNING|{last_tool}"

        # Check 3: Same file read 5+ times (catches incrementing offset/limit evasion)
        if last_tool == "read_file":
            read_paths: list[str] = []
            for msg in reversed(self.messages):
                if msg.role == Role.assistant and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if tc.function and tc.function.name == "read_file":
                            try:
                                args = json.loads(tc.function.arguments or "{}")
                                read_paths.append(args.get("path", ""))
                            except (json.JSONDecodeError, AttributeError):
                                pass
                    if len(read_paths) >= 8:
                        break
            if len(read_paths) >= 5:
                # Count how many times the most recent file was read
                target = read_paths[0]
                count = sum(1 for p in read_paths[:8] if p == target)
                if count >= 5:
                    return f"WARNING|{last_tool}"

        # Check 4: Alternating pattern (A→B→A→B or A→B→C→A→B→C)
        # Catches loops where the model alternates between two tools
        if len(tool_names) >= 8:
            # Check for 2-tool cycle: A B A B A B A B
            last_two = tool_names[-2:]
            if len(last_two) == 2 and last_two[0] != last_two[1]:
                cycle = last_two * 4
                if tool_names[-8:] == cycle:
                    return f"WARNING|{last_tool}"

        # Check 5: Same file written 3+ times (model stuck rewriting one file)
        if last_tool in ("write_file", "search_replace"):
            write_paths: list[str] = []
            for msg in reversed(self.messages):
                if msg.role == Role.assistant and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if tc.function and tc.function.name in ("write_file", "search_replace"):
                            try:
                                args = json.loads(tc.function.arguments or "{}")
                                path = args.get("path", args.get("file_path", ""))
                                if path:
                                    write_paths.append(path)
                            except (json.JSONDecodeError, AttributeError):
                                pass
                if len(write_paths) >= 10:
                    break
            if write_paths:
                from collections import Counter
                path_counts = Counter(write_paths)
                most_common_path, most_common_count = path_counts.most_common(1)[0]
                if most_common_count >= 3:
                    return f"WARNING|{last_tool}"

        return None

    def _log_curiosity_gaps(self, user_msg: str) -> None:
        """Detect unfamiliar-term candidates in the user message and enqueue
        them as UNKNOWN_TERM curiosity items.

        Best-effort: any exception is caught at the call site so a curiosity
        failure never breaks a real user turn. Dedup is handled inside the
        queue (7-day fingerprint window), so calling this on every user
        message is safe.
        """
        try:
            from drydock.curiosity import (
                CuriosityItem, CuriosityKind, detect_gaps, enqueue,
            )
        except Exception:
            return  # module not installed (e.g. minimal test env)
        gaps = detect_gaps(user_msg or "")
        if not gaps:
            return
        session_src = f"session:{getattr(self, 'session_id', '?')}"
        for term in gaps:
            enqueue(CuriosityItem(
                kind=CuriosityKind.UNKNOWN_TERM,
                term=term,
                context=(user_msg or "")[:300],
                source=session_src,
                suggested_action=(
                    "Check whether the project's GraphRAG corpus covers "
                    f"`{term[:80]}`; if not, ingest the relevant path."
                ),
                confidence=0.7,
            ))

    def _auto_prefetch_retrieve(self, user_msg: str) -> None:
        """Auto-fetch relevant chunks from GraphRAG and inject as system note.

        Runs synchronously (the GraphRAG retriever is a fast SQLite query).
        Caps the query length at 300 chars and the injected context at 2000
        chars to avoid blowing context budget on long user prompts.

        Quality gate: only inject if at least one text-chunk hit has score
        >= 8.0 (the indexer's score is roughly TF-IDF magnitude). Below
        that, the retrieval is probably noise and would just bloat the
        prompt.

        See memory/project_graphrag_underused.md for why this exists:
        Gemma 4 doesn't call retrieve() on its own for general-knowledge
        questions, so a curated index is invisible without a hook like
        this one.
        """
        try:
            from drydock.graphrag import Index
        except Exception as e:
            logger.warning("[AUTO-RETRIEVE] setup failed: %s", e, exc_info=True)
            return

        # Extract the actual question from boilerplate. HLE-style prompts
        # are wrapped: "Answer this question. End your response with...
        # QUESTION: <real text>". Without this strip the retrieve query
        # matches scaffolding (CLAUDE.md learnings, etc.) instead of the
        # actual content. Also strip "FINAL ANSWER:" trailing instructions.
        raw = (user_msg or "")
        q_marker = raw.find("QUESTION:")
        if q_marker >= 0:
            raw = raw[q_marker + len("QUESTION:"):]
        # Drop trailing answer-format instructions
        for stopper in ("FINAL ANSWER:", "Your answer", "Format your", "End your response"):
            idx_ = raw.find(stopper)
            if idx_ > 50:  # only strip if there's still meaningful content
                raw = raw[:idx_]
        query = raw.strip()[:400]
        if len(query) < 10:
            logger.warning("[AUTO-RETRIEVE] query too short (%d chars)", len(query))
            return
        logger.warning("[AUTO-RETRIEVE] extracted query: %r", query[:120])

        QUALITY_THRESHOLD = 8.0

        # DB chain: try the primary index first, then fall back to the
        # arXiv corpus (if present) on miss. As of 2026-05-14, 77% of
        # HLE-eval sessions had retrieve return zero above-threshold hits
        # from the primary corpus — for generic STEM questions the arXiv
        # corpus at /data3/arxiv_corpus/graphrag.sqlite (1.18M chunks)
        # has much better recall. The fallback path is operator-tunable
        # via DRYDOCK_GRAPHRAG_FALLBACK_DB; set to empty to disable.
        # Primary DB selection mirrors retrieve._resolve_db_path so the
        # auto-prefetch and the model-issued retrieve calls always agree
        # on which corpus to search:
        #   1. DRYDOCK_GRAPHRAG_DB env override
        #   2. <cwd>/.drydock/graphrag.sqlite (per-project index)
        #   3. ~/.drydock/graphrag.sqlite (home fallback)
        # Without #2, a user with a populated home DB never saw their
        # own project's chunks because home always won.
        env_db = os.environ.get("DRYDOCK_GRAPHRAG_DB")
        if env_db:
            primary_db = env_db
        else:
            project_db = Path.cwd() / ".drydock" / "graphrag.sqlite"
            if project_db.is_file():
                primary_db = str(project_db)
            else:
                primary_db = str(Path.home() / ".drydock" / "graphrag.sqlite")
        fallback_default = "/data3/arxiv_corpus/graphrag.sqlite"
        fallback_db_raw = os.environ.get(
            "DRYDOCK_GRAPHRAG_FALLBACK_DB", fallback_default
        )
        fallback_db = fallback_db_raw if fallback_db_raw else None
        # Don't double-search the same db.
        db_chain: list[str] = [primary_db]
        if fallback_db and Path(fallback_db).resolve() != Path(primary_db).resolve():
            db_chain.append(fallback_db)

        good_hits: list = []
        text_hits: list = []
        used_db: str | None = None
        for db in db_chain:
            if not Path(db).is_file():
                logger.warning("[AUTO-RETRIEVE] db missing: %s", db)
                continue
            try:
                idx = Index(db)
                result = idx.retrieve(query, symbol_limit=0, text_limit=4)
            except Exception as e:
                logger.warning(
                    "[AUTO-RETRIEVE] retrieve failed on %s: %s", db, e
                )
                continue
            hits = getattr(result, "text", None) or getattr(result, "text_hits", []) or []
            gh = [h for h in hits if getattr(h, "score", 0) >= QUALITY_THRESHOLD]
            logger.warning(
                "[AUTO-RETRIEVE] %s: %d total hits, %d above threshold %.1f",
                db, len(hits), len(gh), QUALITY_THRESHOLD,
            )
            if gh:
                text_hits = hits
                good_hits = gh
                used_db = db
                break

        if not good_hits:
            return
        if used_db != primary_db:
            logger.warning(
                "[AUTO-RETRIEVE] primary corpus returned 0 above-threshold "
                "hits; using fallback %s", used_db,
            )

        # Build the system note. Cap at ~2000 chars total.
        chunks = []
        budget = 2000
        for h in good_hits[:3]:
            text = (getattr(h, "content", "") or "").strip()
            score = float(getattr(h, "score", 0))
            file_ = getattr(h, "file", "") or "?"
            s, e = getattr(h, "start_line", 0), getattr(h, "end_line", 0)
            piece = f"--- {file_}:{s}-{e} (score={score:.1f}) ---\n{text}"
            if len(piece) > budget:
                piece = piece[:budget] + "..."
            chunks.append(piece)
            budget -= len(piece) + 4
            if budget <= 0:
                break

        # SYNTHETIC TOOL CALL: instead of mutating the user message
        # (which iter6-9 proved is treated as scaffolding by Gemma 4 — it
        # ignores inline references and trusts its training prior), spawn
        # a fake assistant->tool message pair that LOOKS like the model
        # called retrieve() and got results. Models trust tool outputs
        # as authoritative input.
        #
        # Sequence:
        #   user -> [our synthetic assistant with tool_call retrieve]
        #        -> [our synthetic tool result with the chunks]
        #        -> real LLM turn begins from there
        from drydock.core.types import ToolCall, FunctionCall as _FC
        import uuid

        tool_call_id = f"auto-retrieve-{uuid.uuid4().hex[:16]}"
        # Reflect the CLEANED query (with QUESTION:/FINAL ANSWER: boilerplate
        # stripped) in the synthesized tool_call arguments — not the raw
        # user_msg. Operators reading messages.jsonl could otherwise mistake
        # the noisy full prompt for what BM25 actually scored against, and
        # the model itself sees the same arguments echoed back in compaction.
        tool_args = json.dumps({"query": query[:200]})
        synth_assistant = LLMMessage(
            role=Role.assistant,
            content="",
            tool_calls=[
                ToolCall(
                    id=tool_call_id,
                    function=_FC(name="retrieve", arguments=tool_args),
                    type="function",
                )
            ],
        )
        # Format chunks as the retrieve tool's actual output shape.
        formatted = "=== TEXT ===\n\n" + "\n\n".join(chunks)
        synth_tool = LLMMessage(
            role=Role.tool,
            content=formatted,
            name="retrieve",
            tool_call_id=tool_call_id,
        )
        self.messages.append(synth_assistant)
        self.messages.append(synth_tool)

        # Authoritative-answer recognition. Curated GraphRAG corpora can
        # mark a chunk's verified answer with a literal `ANSWER:` line
        # (also `Answer:`, `Verified answer:`, `Ground truth:`). When
        # auto-prefetch surfaces such a chunk and the BM25 score is high
        # enough that we're confident it matches the user's question,
        # inject a system note telling the model to use that line
        # verbatim — without it, Gemma 4 re-derives from scratch and
        # often overrules the verified value (HLE Phase 0 ablation
        # 2026-05-06: 5/20 with seeded answers because the model
        # ignored its own retrieved ANSWER lines).
        #
        # Only fire when the TOP-1 chunk has the marker. If a lower-
        # scoring chunk has ANSWER (e.g. an unrelated Q's seed bled
        # into the result set), the system note would point the model
        # at the wrong answer (Phase 0' "Nunavut → Ontario" case).
        #
        # Two paths to "authoritative":
        #   (a) absolute: top score >= AUTHORITATIVE_SCORE (works for
        #       long, term-rich questions that yield high BM25)
        #   (b) relative: chunk has the curated header prefix
        #       `===<tag>:<id>===` AND top score outranks 2× the next
        #       hit's score. Catches narrow-trivia cases where BM25
        #       scores are naturally lower (e.g. "What city does X
        #       move to in 1997 movie Y?") but retrieval clearly
        #       picked one curated chunk over the rest.
        import re as _re
        ANSWER_MARKERS = ("ANSWER:", "Answer:", "Verified answer:",
                          "Ground truth:", "Correct answer:")
        CURATED_HEADER_RE = _re.compile(r"^===[a-z][a-z0-9_-]*:\S+===")
        AUTHORITATIVE_SCORE = 100.0  # absolute high-confidence bar
        # Relative-margin path floor. Lowered to 15 (was 30) after the
        # stopword filter (storage._tokenize_query) reduced absolute
        # scores across the board — narrow questions like "Nunavut"
        # legitimately score 20-30 with dominance 5-10× over runners-up.
        # The dominance ratio is the real false-positive guard, not the
        # absolute floor.
        DOMINANCE_SCORE = 15.0       # relative path floor (well above 8.0 noise)
        DOMINANCE_RATIO = 2.0        # top must beat second by this much
        top_chunk = chunks[0] if chunks else ""
        top_score = float(getattr(good_hits[0], "score", 0))
        next_score = (
            float(getattr(good_hits[1], "score", 0))
            if len(good_hits) >= 2 else 0.0
        )
        has_marker = any(marker in top_chunk for marker in ANSWER_MARKERS)
        # Inspect content lines (skip the path/score header that the
        # formatter prepends) for the curated tag.
        chunk_body_lines = top_chunk.split("\n")
        has_curated_header = any(
            CURATED_HEADER_RE.match(line.strip())
            for line in chunk_body_lines[:6]
        )
        is_authoritative = has_marker and (
            top_score >= AUTHORITATIVE_SCORE
            or (
                has_curated_header
                and top_score >= DOMINANCE_SCORE
                and top_score >= DOMINANCE_RATIO * next_score
            )
        )
        if is_authoritative:
            note = (
                "The retrieve tool result above contains a curated "
                "chunk whose question matches the user's. Locate the "
                "line beginning with one of "
                f"{list(ANSWER_MARKERS)} and emit that value verbatim "
                "as your FINAL ANSWER. Do not re-derive — the chunk is "
                "authoritative ground truth provided by the corpus "
                "curator. Respond with text only, no further tool calls."
            )
            self._inject_system_note(note)
        elif chunks:
            # Quality hits but no curated ANSWER marker — common case
            # for arXiv-corpus retrievals. Without a nudge, Gemma 4
            # defaults to chaining web_search calls and burns the
            # session timeout before producing any content (HLE Q4
            # overnight 2026-05-13: 26/30 sessions ended at 481s with
            # last role=tool, no assistant content). The nudge here is
            # advisory — model can still web_search if needed, but it
            # gets told to prefer the retrieval first.
            soft_note = (
                "The retrieve tool result above contains "
                f"{len(chunks)} chunk(s) drawn from the local corpus "
                f"(top score {top_score:.1f}). Read these carefully "
                "and answer from them. Only use web_search if the "
                "retrieved context is clearly insufficient — do not "
                "duplicate the same query you already have evidence "
                "for. When you have enough to answer, respond with "
                "text (no further tool calls)."
            )
            self._inject_system_note(soft_note)

        logger.warning(
            "[AUTO-RETRIEVE] synthesized retrieve tool result: %d chunks "
            "(top score %.1f, content %d chars), msgs now %d, "
            "authoritative=%s",
            len(chunks),
            top_score,
            len(formatted),
            len(self.messages),
            top_score >= AUTHORITATIVE_SCORE and has_marker,
        )

    async def _auto_route_task(self, user_msg: str) -> None:
        """Lightweight auto-context: list project files and key docs.

        Kept minimal to avoid bloating context — just filenames, no content.
        Skip if prompt already has embedded content (TUI path_prompt does this).
        """
        # Skip if the prompt already contains embedded file content (from render_path_prompt)
        if "[SKILL:" in user_msg or "```" in user_msg[:500]:
            return

        parts = []

        try:
            cwd = Path.cwd()
            # List project files (names only, no content)
            py_files = sorted(
                f for f in cwd.rglob("*.py")
                if ".venv" not in str(f) and "__pycache__" not in str(f)
            )
            if py_files:
                listing = "\n".join(f"  {f.relative_to(cwd)}" for f in py_files[:20])
                parts.append(f"PROJECT FILES ({len(py_files)} .py files):\n{listing}")

            # List docs/config files
            for pattern in ("*.md", "*.toml", "*.json", "*.yaml"):
                for f in sorted(cwd.glob(pattern))[:3]:
                    parts.append(f"  {f.name} ({f.stat().st_size}b)")
        except Exception:
            pass

        # Inject discoverable CLI tools (built by DryDock, CLI-Anything, system)
        try:
            from drydock.core.config.harness_files import get_harness_files_manager
            mgr = get_harness_files_manager()
            user_skills_dirs = mgr.user_skills_dirs
            tools_found = []
            for skills_dir in user_skills_dirs:
                if not skills_dir.is_dir():
                    continue
                for skill_dir in sorted(skills_dir.iterdir())[:50]:
                    skill_md = skill_dir / "SKILL.md"
                    if not skill_md.is_file():
                        continue
                    name = skill_dir.name
                    # Only show tool-*, system-*, cli-anything-* skills
                    if not any(name.startswith(p) for p in ("tool-", "system-", "cli-anything-")):
                        continue
                    # Read first line of description
                    try:
                        for line in skill_md.read_text(encoding="utf-8").split("\n"):
                            if line.strip().startswith("description:"):
                                desc = line.split(":", 1)[1].strip().strip('"').strip("'")[:80]
                                tools_found.append(f"  {name}: {desc}")
                                break
                    except Exception:
                        pass
            if tools_found:
                # Show max 15 to avoid bloating context
                parts.append(
                    f"AVAILABLE CLI TOOLS ({len(tools_found)} installed, showing first 15):\n"
                    + "\n".join(tools_found[:15])
                    + "\nUse via bash: cd /path && python3 -m package_name [args]"
                )
        except Exception:
            pass

        if parts:
            self._inject_system_note("\n".join(parts))

    def _ensure_drydock_md(self) -> None:
        """Auto-create DRYDOCK.md in the project root if absent.

        This file is the drydock equivalent of CLAUDE.md / AGENTS.md: a
        per-project instructions file the model loads on every session
        (see system_prompt._load_project_instructions, 16 KB cap).

        We write a LEAN starter (~2 KB) telling the model what tools the
        harness ships with and a few stub sections the user can fill in
        for project-specific guidance. The point is to give the agent
        signal about its own capabilities — especially the math / count /
        memory / verify built-ins it might otherwise overlook — without
        burning context budget on every turn.

        Best practices baked in:
        - Detect the language from manifest files (pyproject / package.json /
          Cargo.toml / go.mod) so the overview line is meaningful.
        - Tool inventory is one bullet line per category, not a treatise.
        - Stub sections (Coding Standards, Workflow) marked TODO so the
          user knows where to add their own rules.
        """
        cwd = Path.cwd()
        if (cwd / "DRYDOCK.md").exists() or (cwd / "drydock.md").exists():
            return

        # Detect language from manifest presence — single short line.
        lang = "Unknown stack"
        for marker, name in (
            ("pyproject.toml", "Python"),
            ("setup.py", "Python"),
            ("requirements.txt", "Python"),
            ("package.json", "JavaScript / TypeScript"),
            ("Cargo.toml", "Rust"),
            ("go.mod", "Go"),
            ("Gemfile", "Ruby"),
            ("pom.xml", "Java (Maven)"),
            ("build.gradle", "Java/Kotlin (Gradle)"),
        ):
            if (cwd / marker).exists():
                lang = name
                break

        try:
            (cwd / "DRYDOCK.md").write_text(
                f"""# DRYDOCK.md — project instructions for the agent

Auto-loaded into the system prompt every session (16 KB cap). Keep it
lean — every byte costs context budget on every turn. Edit freely; this
is a living document.

## Project overview

- **Stack:** {lang} _(detected from manifest)_
- **Purpose:** _(TODO: one sentence on what this project does)_
- **Entry point:** _(TODO: e.g., `python -m mypkg`, `npm start`, `cargo run`)_

## What the harness can do for you

DryDock ships these direct built-in tools (one entry each in the model's
tool list — no MCP overhead):

- **Reads / search:** `read_file`, `glob`, `grep`, `retrieve` (GraphRAG
  semantic search if the project is indexed), `web_search`, `web_fetch`.
- **Writes:** `write_file`, `search_replace` (preferred for edits),
  `bash`, `notebook_edit`.
- **Exact computation (don't compute in your head):**
  - `math(expression="...")` — sandboxed Python: `math.factorial(20)`,
    `Fraction(1,3)+Fraction(1,6)`, `statistics.mean([...])`.
  - `count(pattern="...", text=... OR path=..., mode=...)` —
    substring / regex / lines / words / chars / bytes.
- **Persistent memory across sessions:** `memory(op="save"|"recall"|...)`
  — store and recall key/value notes at `~/.drydock/agent_memory/`.
- **Verify before claiming done:**
  `verify(criterion="...", command="...", expect="...", expect_mode="contains")`
  — runs a check, returns pass/fail. Operationalizes "Loop until
  verified."
- **Delegation:** `task(agent="builder"|"explore"|"diagnostic"|"planner",
  task="...")` — only for genuinely large work (9+ files); inline for
  smaller.

## Behavioral rules (defaults)

1. Don't assume. Don't hide confusion. Surface tradeoffs.
2. Minimum code that solves the problem. Nothing speculative.
3. Touch only what you must. Clean up only your own mess.
4. Define success criteria. Loop until verified (call `verify`).

When you see an unfamiliar named entity (paper title, library, API,
identifier), your FIRST tool call is `retrieve(query="<the term>")` —
not text, not web_search. Investigate before asserting (Curiosity Layer
default).

## Coding standards

- _(TODO: e.g., "prefer named exports", "tabs not spaces", "no `any` in
  TypeScript", "snake_case for Python", language-specific rules here)_

## Workflow

- **Build:** _(TODO: e.g., `npm run build`, `cargo build --release`)_
- **Test:** _(TODO: e.g., `pytest -q`, `npm test`, `cargo test`)_
- **Run:** _(TODO: e.g., `python -m mypkg`, `npm start`)_
- **Format/lint:** _(TODO: e.g., `ruff check . && ruff format .`)_

## External references

- _(TODO: link to the project README, design docs, style guide if any)_

---
_Auto-generated by drydock on first session in this directory. Customize
or replace freely; future sessions will respect your edits._
"""
            )
            logger.info("Auto-created DRYDOCK.md in %s", cwd)
        except (OSError, PermissionError):
            pass  # Non-critical — read-only filesystem

    def _ensure_agents_md(self) -> None:
        """Auto-create AGENTS.md if no project instructions file exists.

        devstral requires a per-project AGENTS.md to use subagents properly.
        Without it, the model loops on bash/ls instead of delegating.
        """
        from drydock.core.paths import AGENTS_MD_FILENAMES

        cwd = Path.cwd()
        # Check if any project instructions file already exists
        for name in AGENTS_MD_FILENAMES:
            if (cwd / name).exists():
                return  # Already has instructions

        # Also check for CLAUDE.md (user might be using Claude Code convention)
        if (cwd / "CLAUDE.md").exists():
            return

        # Create default AGENTS.md — simplified for Gemma 4
        agents_md = cwd / "AGENTS.md"
        try:
            agents_md.write_text(
                "# Project Instructions\n\n"
                "DO NOT ask for confirmation. ACT IMMEDIATELY. Start writing code NOW.\n"
                "If there is a PRD.md, read it then create the files.\n\n"
                "## Workflow\n"
                "1. Read requirements (PRD.md, README, etc.)\n"
                "2. Create __init__.py and __main__.py first\n"
                "3. Create each module file with write_file\n"
                "4. Test: python3 -m package_name --help\n"
                "5. Fix errors and verify\n\n"
                "## Rules\n"
                "- Use absolute imports: `from package.module import X`\n"
                "- Always create `__init__.py` and `__main__.py`\n"
                "- Create ALL files listed in the PRD before stopping\n"
                "- Do NOT stop after creating just __init__.py — continue to the next file\n"
                "- NEVER ask 'should I proceed' or 'would you like me to' — JUST DO IT\n"
                "- After creating a file, immediately create the next one\n"
            )
            logger.info("Auto-created AGENTS.md in %s", cwd)
        except (OSError, PermissionError):
            pass  # Non-critical — read-only filesystem or no permissions

    def _is_build_task(self, user_msg: str) -> bool:
        """Detect if a user message is an explicit build task that needs orchestration.

        Only triggers on clear build intent — NOT on "review" or "look at".
        """
        msg_lower = user_msg.lower()

        # Explicit build verbs — user clearly wants to create something
        has_build_verb = any(kw in msg_lower for kw in (
            "build", "create a", "implement", "scaffold",
            "build the project", "build this project",
            "build from prd", "build it",
            "get started building", "start building",
        ))

        # "review", "look at", "read" are NOT build verbs
        is_review = any(kw in msg_lower for kw in (
            "review", "look at", "read", "check", "analyze", "audit",
            "what does", "explain", "summarize",
        ))
        if is_review and not has_build_verb:
            return False

        has_prd = Path.cwd().joinpath("PRD.md").exists() or Path.cwd().joinpath("prd.md").exists()
        return has_build_verb and (has_complexity or has_prd)

    def _auto_fix_package(self, file_path: str) -> None:
        """Silently fix common packaging mistakes after model writes a file.

        The model (devstral-24B) consistently fails at:
        1. Creating __main__.py for packages
        2. Using relative imports instead of absolute
        This runs after every write_file/search_replace and fixes both.
        """
        try:
            fp = Path(file_path)
            if not fp.exists() or not fp.is_file():
                return

            pkg_dir = fp.parent
            init_file = pkg_dir / "__init__.py"

            # Only fix files inside a package (has __init__.py)
            if not init_file.exists():
                return

            pkg_name = pkg_dir.name

            # 1. Create __main__.py if missing
            main_file = pkg_dir / "__main__.py"
            if not main_file.exists():
                # Find the most likely entry point (cli.py, main.py, app.py)
                entry = None
                entry_func = "main"
                for candidate in ["cli.py", "main.py", "app.py", "__init__.py"]:
                    cand_path = pkg_dir / candidate
                    if cand_path.exists() and candidate != "__init__.py":
                        # Check if it has a main() function
                        try:
                            content = cand_path.read_text()
                            if "def main(" in content:
                                entry = candidate[:-3]  # strip .py
                                break
                        except Exception:
                            pass
                if entry:
                    main_content = (
                        f"from {pkg_name}.{entry} import {entry_func}\n\n"
                        f"if __name__ == \"__main__\":\n"
                        f"    {entry_func}()\n"
                    )
                    main_file.write_text(main_content)
                    logger.info("Auto-created %s/__main__.py (entry: %s.%s)", pkg_name, entry, entry_func)

            # 2. Fix relative imports → absolute imports
            try:
                content = fp.read_text()
                if f"from .{'' if content else ''}" in content:
                    import re
                    # Replace from .module import X → from pkg.module import X
                    fixed = re.sub(
                        r"from \.([\w.]+) import",
                        f"from {pkg_name}.\\1 import",
                        content,
                    )
                    # Replace from . import X → from pkg import X
                    fixed = re.sub(
                        r"from \. import",
                        f"from {pkg_name} import",
                        fixed,
                    )
                    if fixed != content:
                        fp.write_text(fixed)
                        logger.info("Auto-fixed relative imports in %s", fp.name)
            except Exception:
                pass

        except Exception as e:
            logger.debug("Auto-fix package failed for %s: %s", file_path, e)

    def _build_auto_context(self, user_msg: str) -> str | None:
        """Build auto-delegation context based on the prompt and project state.

        Instead of hoping the model calls task()/invoke_skill(), we inject:
        1. Project file listing (if files exist) — replaces explore subagent
        2. Skill content (if prompt matches a skill) — replaces invoke_skill
        3. Planning prompt (if complex build task) — replaces planner subagent
        """
        parts: list[str] = []
        msg_lower = user_msg.lower()

        # 1. Auto-explore: list project files so model doesn't have to
        try:
            cwd = Path.cwd()
            py_files = sorted(cwd.rglob("*.py"))
            py_files = [f for f in py_files if ".logs" not in str(f) and ".venv" not in str(f)]
            if len(py_files) >= 3:
                listing = "\n".join(f"  {f.relative_to(cwd)}" for f in py_files[:30])
                parts.append(f"PROJECT FILES ({len(py_files)} Python files):\n{listing}")
                if len(py_files) > 30:
                    parts.append(f"  ... and {len(py_files) - 30} more")
        except Exception:
            pass

        # Also list non-Python files of interest
        try:
            for pattern in ("*.md", "*.txt", "*.json", "*.yaml", "*.yml", "*.toml", "*.csv"):
                for f in sorted(cwd.glob(pattern))[:5]:
                    if f.name not in (".logs",):
                        parts.append(f"  {f.name} ({f.stat().st_size} bytes)")
        except Exception:
            pass

        # 2. Skill list removed from auto-context — Gemma 4 auto-invokes skills
        # when it sees them listed, causing template leaks and wasted turns.
        # Skills are available via /slash commands and invoke_skill tool but
        # the model should focus on using tools directly.

        # 3. Subagent descriptions for delegation
        parts.append(
            "SUBAGENTS (use task tool to delegate complex exploration):\n"
            "  task(task='...', agent='explore') — Read-only codebase exploration\n"
            "  task(task='...', agent='diagnostic') — Debug/investigate with bash access\n"
            "  task(task='...', agent='planner') — Plan multi-file changes before coding"
        )

        # 3. Planning nudge for complex build tasks
        is_build = any(kw in msg_lower for kw in (
            "build", "create", "implement", "make a", "write a",
            "set up", "scaffold", "get started", "prd",
        ))
        is_complex = len(user_msg) > 100 or any(kw in msg_lower for kw in (
            "multiple", "modules", "package", "api", "database",
            "features", "cli", "commands",
        ))
        if is_build and is_complex:
            parts.append(
                "MANDATORY BUILD RULES:\n"
                "1. Plan first: list ALL files you will create\n"
                "2. ALWAYS create __main__.py so 'python3 -m package_name' works:\n"
                "   ```python\n"
                "   from package_name.cli import main\n"
                "   if __name__ == '__main__':\n"
                "       main()\n"
                "   ```\n"
                "3. Use ABSOLUTE imports: 'from pkg.module import X', NOT 'from .module import X'\n"
                "4. Create test/sample data files BEFORE testing (the tool needs input to work)\n"
                "5. Test with: python3 -m package_name (NOT python3 package/file.py)\n"
                "6. If a test fails, read the error, fix with search_replace, then retry\n"
                "7. After 1-2 successful tests, STOP and tell the user it's done"
            )
        elif is_build:
            parts.append(
                "BUILD RULES: Use write_file to create files. Use absolute imports. "
                "Create __main__.py for packages. Create test data before testing. "
                "Test with python3 -m package_name (not python3 package/file.py)."
            )

        if not parts:
            return None
        return "\n\n".join(parts)

    def _recent_tool_names(self, limit: int = 10) -> list[str]:
        """Return recent tool names from message history (most recent last)."""
        names: list[str] = []
        for msg in reversed(self.messages):
            if msg.role == Role.assistant and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function and tc.function.name:
                        names.append(tc.function.name)
            if len(names) >= limit:
                break
        names.reverse()
        return names

    def _prune_repeated_tool_calls(self) -> None:
        """Remove duplicate tool call/result pairs from recent history.

        When the model is stuck in a loop, the repeated context reinforces
        the loop behavior. By pruning duplicates and keeping only the first
        occurrence + one recent one, we give the model a cleaner context
        to work from.
        """
        if len(self.messages) < 10:
            return

        # Find sequences of identical tool calls (assistant+tool pairs)
        # by comparing tool call signatures
        seen_sigs: dict[str, int] = {}
        indices_to_remove: list[int] = []

        i = 0
        while i < len(self.messages) - 2:  # Keep at least last 2 messages
            msg = self.messages[i]
            if msg.role == Role.assistant and msg.tool_calls:
                # Compute signature (normalize read_file to path-only so
                # reads of the same file with different offset/limit are duplicates)
                call_parts = []
                for tc in msg.tool_calls:
                    if tc.function:
                        fn_name = tc.function.name or ""
                        fn_args = tc.function.arguments or ""
                        if fn_name == "read_file":
                            try:
                                parsed = json.loads(fn_args)
                                fn_args = parsed.get("path", fn_args)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        call_parts.append(f"{fn_name}:{fn_args}")
                sig = hashlib.sha256("|".join(sorted(call_parts)).encode()).hexdigest()[:16]

                if sig in seen_sigs:
                    # Duplicate — mark for removal (assistant msg + next tool result)
                    indices_to_remove.append(i)
                    if i + 1 < len(self.messages) - 2 and self.messages[i + 1].role == Role.tool:
                        indices_to_remove.append(i + 1)
                else:
                    seen_sigs[sig] = i
            i += 1

        if indices_to_remove:
            logger.info("Pruning %d duplicate messages from history (had %d)", len(indices_to_remove), len(self.messages))
            self.messages.reset([m for j, m in enumerate(self.messages) if j not in set(indices_to_remove)])
            self._fill_missing_tool_responses()
            self._ensure_assistant_after_tools()

    def _clean_message_history(self) -> None:
        ACCEPTABLE_HISTORY_SIZE = 2
        if len(self.messages) < ACCEPTABLE_HISTORY_SIZE:
            return
        self._fill_missing_tool_responses()
        self._ensure_assistant_after_tools()

    def _fill_missing_tool_responses(self) -> None:
        i = 1
        while i < len(self.messages):  # noqa: PLR1702
            msg = self.messages[i]

            if msg.role == "assistant" and msg.tool_calls:
                expected_responses = len(msg.tool_calls)

                if expected_responses > 0:
                    actual_responses = 0
                    j = i + 1
                    while j < len(self.messages) and self.messages[j].role == "tool":
                        actual_responses += 1
                        j += 1

                    if actual_responses < expected_responses:
                        insertion_point = i + 1 + actual_responses

                        for call_idx in range(actual_responses, expected_responses):
                            tool_call_data = msg.tool_calls[call_idx]

                            empty_response = LLMMessage(
                                role=Role.tool,
                                tool_call_id=tool_call_data.id or "",
                                name=(
                                    (tool_call_data.function.name or "")
                                    if tool_call_data.function
                                    else ""
                                ),
                                content=str(
                                    get_user_cancellation_message(
                                        CancellationReason.TOOL_NO_RESPONSE
                                    )
                                ),
                            )

                            self.messages.insert(insertion_point, empty_response)
                            insertion_point += 1

                    i = i + 1 + expected_responses
                    continue

            i += 1

    def _ensure_assistant_after_tools(self) -> None:
        MIN_MESSAGE_SIZE = 2
        if len(self.messages) < MIN_MESSAGE_SIZE:
            return

        last_msg = self.messages[-1]
        if last_msg.role is Role.tool:
            # Bridge tool→user gap. "Continuing..." was ambiguous — Gemma 4
            # read it as a self-statement ("I said Continuing, so I'm done")
            # and went silent for the next user prompt. In the 2026-04-16
            # stress run this single filler poisoned 14/15 prompts per cycle.
            # An explicit hand-off phrases it as a clear turn boundary.
            filler = LLMMessage(
                role=Role.assistant,
                content="Previous turn ended; awaiting your next instruction.",
            )
            self.messages.append(filler)

    def _reset_session(self) -> None:
        self.session_id = str(uuid4())
        self.session_logger.reset_session(self.session_id)

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        self.approval_callback = callback

    def set_user_input_callback(self, callback: UserInputCallback) -> None:
        self.user_input_callback = callback

    # ------------------------------------------------------------------
    # Goal pursuit (Claude Code /goal feature) — see drydock/core/goal.py
    # ------------------------------------------------------------------

    def set_goal(self, condition: str, max_iterations: int = 20) -> None:
        """Activate goal-pursuit mode. The TUI calls this when the user
        types `/goal <condition>`. The agent loop itself doesn't act on
        the goal — the TUI's post-turn hook checks `self.goal` and
        decides whether to inject a continuation prompt."""
        from drydock.core.goal import GoalState
        self.goal = GoalState(
            condition=condition.strip(),
            max_iterations=max_iterations,
        )
        logger.warning(
            "[goal] activated: %r (cap=%d turns)",
            condition[:80], max_iterations,
        )

    def clear_goal(self) -> None:
        """Cancel goal-pursuit. Idempotent."""
        if getattr(self, "goal", None) is not None:
            logger.warning("[goal] cleared")
        self.goal = None

    async def evaluate_goal(self) -> tuple[str, str]:
        """Ask the model whether the active goal has been met.

        Returns (verdict, reasoning) where verdict ∈ {"YES", "NO", "ERROR"}.
        ERROR means the call itself failed or the response couldn't be
        parsed — caller should treat as NO and continue (or, after a
        threshold of ERRORs in a row, clear the goal as a safety hatch).
        """
        from drydock.core.goal import (
            EVALUATOR_SYSTEM_PROMPT,
            build_evaluator_prompt,
            collect_recent_message_snippets,
            parse_verdict,
        )
        goal = getattr(self, "goal", None)
        if goal is None or not goal.active:
            return ("ERROR", "no active goal")

        snippets = collect_recent_message_snippets(self.messages, n=8)
        user_prompt = build_evaluator_prompt(goal, snippets)

        eval_messages = [
            LLMMessage(role=Role.system, content=EVALUATOR_SYSTEM_PROMPT),
            LLMMessage(role=Role.user, content=user_prompt),
        ]
        active_model = self.config.get_active_model()
        try:
            # Tight budget — evaluator returns ~30 tokens at most.
            # Temperature low for determinism. No tools.
            result = await self.backend.complete(
                model=active_model,
                messages=eval_messages,
                temperature=0.0,
                tools=[],
                tool_choice=None,
                extra_headers=self._get_extra_headers(
                    self.config.get_active_provider()
                ),
                max_tokens=120,
                metadata=None,
            )
        except Exception as e:  # noqa: BLE001 — never crash the TUI on eval error
            logger.warning("[goal] evaluator call failed: %s", e)
            return ("ERROR", f"evaluator backend error: {e!s}"[:200])

        raw = (result.message.content or "").strip()
        verdict, reasoning = parse_verdict(raw)
        goal.last_verdict = verdict
        goal.last_evaluator_reasoning = reasoning
        logger.warning(
            "[goal] verdict=%s iter=%d/%d reason=%r",
            verdict, goal.iterations, goal.max_iterations, reasoning[:120],
        )
        return (verdict, reasoning)

    async def undo_last_turn(self) -> tuple[bool, str]:
        """Rewind history past the LAST user message, dropping the
        assistant turn (and any tool results) it triggered AND the
        user message itself. Use case: the last user prompt set off
        a chain that wedged the conversation; the user wants to back
        out and try a different prompt.

        Returns (success, info_message).

        Why this is safer than `/clear`:
          - Preserves the system message (index 0)
          - Preserves all prior good user+assistant exchanges
          - Resets the sticky error counters so the new prompt
            won't immediately re-trip the lockout

        Why drop the user message too (not just the assistant turn):
          - If the user repeats the same prompt, they'll re-trigger
            the same bad assistant response. The point of /undo is
            to escape; the user can always re-type the prompt if
            they really want it.
        """
        # Find the last user message — walk backward
        last_user_idx = -1
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].role == Role.user:
                last_user_idx = i
                break

        if last_user_idx <= 0:
            # No user message to rewind past (only the system message
            # is present), or the user message is at idx 0 which we
            # never drop. Nothing to undo.
            return (False, "Nothing to undo — no prior user turn in history.")

        dropped = len(self.messages) - last_user_idx
        kept = list(self.messages[:last_user_idx])
        try:
            await self.session_logger.save_interaction(
                self.messages,
                self.stats,
                self._base_config,
                self.tool_manager,
                self.agent_profile,
            )
        except Exception as e:  # noqa: BLE001 — never block /undo on a save failure
            logger.warning("[undo] session save failed (continuing): %s", e)
        self.messages.reset(kept)
        # Clear the sticky error counters so the next prompt starts fresh.
        if hasattr(self, "_total_error_rounds"):
            self._total_error_rounds = 0
        if hasattr(self, "_consecutive_circuit_breaker_fires"):
            self._consecutive_circuit_breaker_fires = 0
        if hasattr(self, "_consecutive_empty_turns"):
            self._consecutive_empty_turns = 0
        logger.warning(
            "[undo] rolled back: kept %d messages (dropped %d after last user idx=%d)",
            len(kept), dropped, last_user_idx,
        )
        return (
            True,
            f"Rolled back the last turn — dropped {dropped} message(s). "
            f"Type your next prompt to continue from the prior state.",
        )

    async def clear_history(self) -> None:
        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self._base_config,
            self.tool_manager,
            self.agent_profile,
        )
        self.messages.reset(self.messages[:1])

        self.stats = AgentStats.create_fresh(self.stats)
        self.stats.trigger_listeners()

        try:
            active_model = self.config.get_active_model()
            self.stats.update_pricing(
                active_model.input_price, active_model.output_price
            )
        except ValueError:
            pass

        self.middleware_pipeline.reset()
        self.tool_manager.reset_all()
        self._reset_session()

        # ALSO reset all agent-level sampling/loop/circuit-breaker state.
        # Learning from the 2026-04-15 stress marathon: sticky loop flags
        # (freq_penalty=0.4 baked into subsequent generations) were the
        # cause of the user-visible "no spaces in TUI text" bug. If a
        # user hits /clear after a bad turn and this state DOESN'T
        # reset, the fresh session inherits the poisoning.
        self._tool_call_history = {}
        self._consecutive_circuit_breaker_fires = 0
        self._empty_responses = 0
        self._successful_test_runs = 0
        self._loop_detected = False
        self._loop_signal = None
        self._hot_tool_path = None
        self._consecutive_empty_turns = 0
        self._empty_nudge_last_user_idx = -1
        self._total_error_rounds = 0
        self._read_file_state = {}
        # /goal state: None means no active goal. Cleared by /clear and
        # /compact since the goal is session-scoped and a fresh session
        # shouldn't inherit the prior pursuit.
        self.goal = None

    async def compact(self) -> str:
        try:
            self._clean_message_history()
            await self.session_logger.save_interaction(
                self.messages,
                self.stats,
                self._base_config,
                self.tool_manager,
                self.agent_profile,
            )

            summary_request = UtilityPrompt.COMPACT.read()
            self.stats.steps += 1

            with self.messages.silent():
                self.messages.append(
                    LLMMessage(role=Role.user, content=summary_request)
                )
                summary_result = await self._chat()

            if summary_result.usage is None:
                raise AgentLoopLLMResponseError(
                    "Usage data missing in compaction summary response"
                )
            summary_content = summary_result.message.content or ""

            system_message = self.messages[0]
            summary_message = LLMMessage(role=Role.user, content=summary_content)
            self.messages.reset([system_message, summary_message])

            active_model = self.config.get_active_model()
            provider = self.config.get_provider_for_model(active_model)

            actual_context_tokens = await self.backend.count_tokens(
                model=active_model,
                messages=self.messages,
                tools=self.format_handler.get_available_tools(self.tool_manager),
                extra_headers={"user-agent": get_user_agent(provider.backend)},
                metadata=self.entrypoint_metadata.model_dump()
                if self.entrypoint_metadata
                else None,
            )

            self.stats.context_tokens = actual_context_tokens

            self._reset_session()

            # Reset agent-level state derived from prior context, same
            # as /clear. After compact, the OLD messages are gone — so
            # circuit-breaker counts, loop signals, hot-path mutes,
            # and read-state tracking based on those messages are stale.
            # Without this, freq_penalty stickiness etc. would survive
            # across compact and re-poison the new compacted session.
            # Keeps _successful_test_runs and stats since those reflect
            # the user's actual progress (visible in the summary).
            self._tool_call_history = {}
            self._consecutive_circuit_breaker_fires = 0
            self._loop_detected = False
            self._loop_signal = None
            self._hot_tool_path = None
            self._consecutive_empty_turns = 0
            self._empty_nudge_last_user_idx = -1
            self._total_error_rounds = 0
            self._read_file_state = {}

            await self.session_logger.save_interaction(
                self.messages,
                self.stats,
                self._base_config,
                self.tool_manager,
                self.agent_profile,
            )

            self.middleware_pipeline.reset(reset_reason=ResetReason.COMPACT)

            return summary_content or ""

        except Exception:
            await self.session_logger.save_interaction(
                self.messages,
                self.stats,
                self._base_config,
                self.tool_manager,
                self.agent_profile,
            )
            raise

    async def switch_agent(self, agent_name: str) -> None:
        if agent_name == self.agent_profile.name:
            return
        self.agent_manager.switch_profile(agent_name)
        await self.reload_with_initial_messages(reset_middleware=False)

    async def reload_with_initial_messages(
        self,
        base_config: DrydockConfig | None = None,
        max_turns: int | None = None,
        max_price: float | None = None,
        reset_middleware: bool = True,
    ) -> None:
        # Force an immediate yield to allow the UI to update before heavy sync work.
        # When there are no messages, save_interaction returns early without any await,
        # so the coroutine would run synchronously through ToolManager, SkillManager,
        # and system prompt generation without yielding control to the event loop.
        await asyncio.sleep(0)

        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self._base_config,
            self.tool_manager,
            self.agent_profile,
        )

        if base_config is not None:
            self._base_config = base_config
            self.agent_manager.invalidate_config()

        self.backend = self.backend_factory()

        if max_turns is not None:
            self._max_turns = max_turns
        if max_price is not None:
            self._max_price = max_price

        self.tool_manager = ToolManager(
            lambda: self.config, mcp_registry=self._mcp_registry
        )
        self.skill_manager = SkillManager(lambda: self.config)

        new_system_prompt = get_universal_system_prompt(
            self.tool_manager, self.config, self.skill_manager, self.agent_manager
        )

        self.messages.reset([
            LLMMessage(role=Role.system, content=new_system_prompt),
            *[msg for msg in self.messages if msg.role != Role.system],
        ])

        if len(self.messages) == 1:
            self.stats.reset_context_state()

        try:
            active_model = self.config.get_active_model()
            self.stats.update_pricing(
                active_model.input_price, active_model.output_price
            )
        except ValueError:
            pass

        if reset_middleware:
            self._setup_middleware()
