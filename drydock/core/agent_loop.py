from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from enum import StrEnum, auto
import hashlib
from http import HTTPStatus
import json
import logging
from pathlib import Path
from threading import Thread
import time
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel

from drydock.cli.terminal_setup import detect_terminal
from drydock.core.agents.manager import AgentManager
from drydock.core.agents.models import AgentProfile, BuiltinAgentName
from drydock.core.config import Backend, ProviderConfig, VibeConfig
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
    VIBE_STOP_EVENT_TAG,
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
REPEAT_WARNING_THRESHOLD = 4  # Same exact call 4+ times before warning
REPEAT_FORCE_STOP_THRESHOLD = 8  # Same exact call 8+ times before force-stop

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
        config: VibeConfig,
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

        thread = Thread(
            target=migrate_sessions_entrypoint,
            args=(config.session_logging,),
            daemon=True,
            name="migrate_sessions",
        )
        thread.start()

    @property
    def agent_profile(self) -> AgentProfile:
        return self.agent_manager.active_profile

    @property
    def config(self) -> VibeConfig:
        return self.agent_manager.config

    @property
    def auto_approve(self) -> bool:
        return self.config.auto_approve

    def set_tool_permission(
        self, tool_name: str, permission: ToolPermission, save_permanently: bool = False
    ) -> None:
        if save_permanently:
            VibeConfig.save_updates({
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

        # Auto-create AGENTS.md if no project instructions exist.
        # devstral needs per-project AGENTS.md to anchor its behavior —
        # without it the model loops on ls/bash instead of using subagents.
        if self.stats.steps <= 1:
            self._ensure_agents_md()

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

    def teleport_to_vibe_nuage(
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
        if active_model.auto_compact_threshold > 0:
            self.middleware_pipeline.add(
                AutoCompactMiddleware(active_model.auto_compact_threshold)
            )
            if self.config.context_warnings:
                self.middleware_pipeline.add(
                    ContextWarningMiddleware(0.5, active_model.auto_compact_threshold)
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
                    content=f"<{VIBE_STOP_EVENT_TAG}>{result.reason}</{VIBE_STOP_EVENT_TAG}>",
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

        yield UserMessageEvent(content=user_msg, message_id=user_message.message_id)

        # === AUTO-CONTEXT ===
        # Auto-explore project files + inject relevant skill for the task type.
        # The model handles everything with subagents (v2.0.0 fixed delegation).
        _ar_t0 = time.perf_counter()
        await self._auto_route_task(user_msg)
        _ar_t1 = time.perf_counter()
        logger.warning("[TIMING] _auto_route_task: %.2fs", _ar_t1 - _ar_t0)

        try:
            should_break_loop = False
            tool_turns = 0
            api_error_count = 0
            has_made_edit = False  # Track if model has used search_replace/write_file
            # Per-user-prompt wall-clock budget. Gemma 4 routinely
            # spends 60+ minutes on a single prompt without closing it
            # — looks like work is happening, but the user is staring
            # at a non-responsive TUI. Cap at 15 minutes per prompt.
            PER_PROMPT_BUDGET_SEC = 15 * 60
            _prompt_start = time.perf_counter()
            logger.warning("[TIMING] entering conversation while loop")
            while not should_break_loop:
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
                if tool_turns == 15:
                    self._inject_system_note(
                        f"You have used {tool_turns} tool calls on this "
                        "single user request. Start wrapping up — make "
                        "your next 3-5 calls count, then stop with a "
                        "summary so the user can review."
                    )
                elif tool_turns == 25:
                    self._inject_system_note(
                        f"You have used {tool_turns} tool calls on this "
                        "single request. STOP NOW. Emit a final text "
                        "response summarizing what you did (or what is "
                        "blocked) so the user can take the next step."
                    )
                elif tool_turns >= 35:
                    # Hard end-of-turn: synthesize a user-facing message
                    # and stop. The model has spent way too long on one
                    # request without closing it.
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
                            yield AssistantEvent(
                                content=f"\n\n[Stopping: {self._total_error_rounds * MAX_API_ERRORS}+ API errors. The model cannot process this request. Try /compact or /clear to free context.]\n",
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
                          or "status: 400" in error_str.lower()):
                        # Context limit or malformed request — aggressive recovery
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
                        error_text = "Context compacted due to API error. Continue with your task."
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

                should_break_loop = last_message.role != Role.tool

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
                _dbg(f"[STALL-DEBUG] max retries ({MAX_STALL_RETRIES}) exhausted; leaving empty")
                break
            prev_role = self.messages[-2].role if len(self.messages) >= 2 else None
            if prev_role not in (Role.tool, Role.user):
                _dbg(f"[STALL-DEBUG] prev_role={prev_role} not recoverable")
                break

            _dbg(f"[STALL-DEBUG] inline retry #{_stall_attempt + 1} (prev={prev_role})")
            # Pop the empty assistant; inject an escalating nudge.
            self.messages.pop()
            if _stall_attempt == 0:
                note = (
                    "Continue working. Use a tool (read_file, "
                    "write_file, search_replace, bash) or state "
                    "your plan in text."
                )
            elif _stall_attempt == 1:
                note = (
                    "You sent an empty response. Call a tool now "
                    "(write_file, search_replace, bash, read_file) "
                    "OR explicitly say you are done with this task."
                )
            else:
                note = (
                    "You have sent 3 empty responses in a row for "
                    "this user request. Respond with either (a) a "
                    "tool call to make progress, or (b) one "
                    "sentence explaining why you cannot proceed."
                )
            self._inject_system_note(note)
            logger.info(
                "Empty-response stall (inline retry %d/%d, prev=%s)",
                _stall_attempt + 1, MAX_STALL_RETRIES, prev_role,
            )
            # Loop back to re-call the LLM.
            continue

        # (Old stall check removed — now handled inline above in the
        # retry loop, which re-calls the LLM after each empty rather
        # than returning control to the outer loop that would exit on
        # empty assistant + user-role precursor.)

        # Detect repetitive text generation (Gemma 4 sometimes loops text within one response)
        if last_message.content and len(last_message.content) > 200:
            text = last_message.content
            # Check if any sentence repeats 3+ times
            sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 30]
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

        if not resolved.tool_calls and not resolved.failed_calls:
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

        Conservative thresholds — only fires on truly pathological
        repeat counts that would never be a "valid retry":
          search_replace, write_file, bash: after 8 identical calls
          read_file, grep, glob, ls: after 12 identical calls
        """
        args_str = json.dumps(tool_call.args_dict, sort_keys=True, default=str)
        sig = hashlib.sha256(
            f"{tool_call.tool_name}:{args_str}".encode()
        ).hexdigest()
        count, last_result = self._tool_call_history.get(sig, (0, ""))
        tool_name = tool_call.tool_name
        is_readonly = tool_name in ("grep", "read_file", "glob", "ls")
        threshold = 12 if is_readonly else 8
        if count < threshold:
            return None
        return (
            f"NOTE: this exact call to `{tool_name}` has been made "
            f"{count} times this session with identical arguments. "
            f"Last result: {last_result[:200]}\n\n"
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
        self._tool_call_history[sig] = (count + 1, result_text[:500])

    async def _process_one_tool_call(
        self, tool_call: ResolvedToolCall
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent]:
        # Circuit breaker: block exact duplicate calls after 2 attempts
        if blocked := self._circuit_breaker_check(tool_call):
            self._consecutive_circuit_breaker_fires += 1

            if self._consecutive_circuit_breaker_fires >= 5:
                # Model is ignoring the circuit breaker — force stop
                force_msg = (
                    f"FORCED STOP: You ignored the circuit breaker {self._consecutive_circuit_breaker_fires} times. "
                    f"The session is being terminated because you keep running the same command. "
                    f"You MUST try a completely different approach."
                )
                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    error=force_msg,
                    tool_call_id=tool_call.call_id,
                )
                self._handle_tool_response(tool_call, force_msg, "failure")
                # Inject stop signal into messages
                yield AssistantEvent(
                    content=f"\n\n[{force_msg}]\n",
                    stopped_by_middleware=True,
                )
                return

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

    async def _handle_tool_calls(
        self, resolved: ResolvedMessage
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent | ToolStreamEvent | AssistantEvent]:
        async for event in self._emit_failed_tool_events(resolved.failed_calls):
            yield event
        for tool_call in resolved.tool_calls:
            # Stop processing more tool calls if circuit breaker force-stopped
            if self._consecutive_circuit_breaker_fires >= 5:
                break

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
        KEEP_RECENT = 6              # last N tool messages stay full
        SOFT_CAP_BYTES = 800         # tool result longer than this gets shrunk
        HEAD_BYTES = 400             # bytes kept from the head
        TAIL_BYTES = 100             # bytes kept from the tail

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
            # Idempotent guard — already truncated by us
            if "[…truncated " in content and "bytes…]" in content:
                continue
            head = content[:HEAD_BYTES]
            tail = content[-TAIL_BYTES:]
            removed = len(content) - HEAD_BYTES - TAIL_BYTES
            msg.content = (
                f"{head}\n[…truncated {removed} bytes…]\n{tail}"
            )

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

        # Fix 2: If last message is assistant, add a user "Continue." prompt
        if self.messages and self.messages[-1].role == Role.assistant:
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
            # System note / loop nudge → act immediately
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
        if getattr(self, "_loop_detected", False) and getattr(self, "_loop_signal", "") == "FORCE_STOP":
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

            # Token-level loop-breaker: when repetition is detected, bump
            # temperature and add frequency_penalty + a fresh seed so the
            # model's next completion is mechanically likely to diverge.
            # Mistral/OpenAI-compat backends pass these straight through
            # to vLLM's SamplingParams.
            if getattr(self, "_loop_detected", False):
                signal = getattr(self, "_loop_signal", "") or ""
                # Heavier bump if we've already hit the FORCE_STOP signal
                # (=8 repeats) vs a WARNING (=3-5 repeats).
                heavy = signal == "FORCE_STOP"
                temp = min(1.0, temp + (0.5 if heavy else 0.3))
                extra_sampling = {
                    "frequency_penalty": 0.7 if heavy else 0.4,
                    "presence_penalty": 0.3,
                    "seed": int(time.time() * 1000) & 0x7FFFFFFF,
                }
                logger.info(
                    "[LOOP-BREAK] %s → temp %.2f, freq_pen %.2f, seed %d",
                    signal, temp, extra_sampling["frequency_penalty"],
                    extra_sampling["seed"],
                )

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
                    msg.content = (msg.content or "") + f"\n\n[SYSTEM: {text}]"
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

        # Early check: search_replace with the same file + old_string twice
        # in a row.  This is the #1 user-pain loop — the model retries an
        # edit that already succeeded or that keeps failing with the same
        # "not found" error.  Nudge after just 2 identical attempts.
        recent_sr: list[str] = []
        for msg in reversed(self.messages[-20:]):
            if msg.role == Role.assistant and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function and tc.function.name == "search_replace":
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            # Build a key from file_path + old_string (content block)
                            key = f"{args.get('file_path', '')}:{args.get('content', '')}"
                            recent_sr.append(key)
                        except (json.JSONDecodeError, AttributeError):
                            pass
                if len(recent_sr) >= 4:
                    break
        if len(recent_sr) >= 2 and recent_sr[0] == recent_sr[1]:
            return "WARNING|search_replace"

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

        # Check 1: Exact same tool call repeated (same name + same args)
        last_tool = tool_names[-1] if tool_names else ""
        if (
            len(sigs) >= REPEAT_FORCE_STOP_THRESHOLD
            and all(s == sigs[-1] for s in sigs[-REPEAT_FORCE_STOP_THRESHOLD:])
        ):
            return "FORCE_STOP"

        # Check 1a: Same TOOL NAME repeated ≥8 consecutively, regardless
        # of args. Catches the "write_file with missing/corrupted args 36
        # times in a row" pathology where each sig differs (path is
        # missing or garbled differently each call) but the model is
        # clearly stuck hammering the same tool.
        # Record a hot-combo on the stuck tool with an empty-path marker
        # so the per-tool mute in _chat will remove it for 1 turn.
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
        if last_tool in ("bash", "run_command"):
            same_tool_limit = 5
        elif last_tool in ("grep", "read_file"):
            same_tool_limit = 7  # investigation tools need some room
        else:
            same_tool_limit = 5
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
            empty_assistant_msg = LLMMessage(role=Role.assistant, content="Continuing...")
            self.messages.append(empty_assistant_msg)

    def _reset_session(self) -> None:
        self.session_id = str(uuid4())
        self.session_logger.reset_session(self.session_id)

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        self.approval_callback = callback

    def set_user_input_callback(self, callback: UserInputCallback) -> None:
        self.user_input_callback = callback

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
        base_config: VibeConfig | None = None,
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
