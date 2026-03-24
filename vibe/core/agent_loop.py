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

from vibe.cli.terminal_setup import detect_terminal
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentProfile, BuiltinAgentName
from vibe.core.config import Backend, ProviderConfig, VibeConfig
from vibe.core.llm.backend.factory import BACKEND_FACTORY
from vibe.core.llm.exceptions import BackendError
from vibe.core.llm.format import (
    APIToolFormatHandler,
    FailedToolCall,
    ResolvedMessage,
    ResolvedToolCall,
)
from vibe.core.llm.types import BackendLike
from vibe.core.middleware import (
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
from vibe.core.plan_session import PlanSession
from vibe.core.prompts import UtilityPrompt
from vibe.core.session.session_logger import SessionLogger
from vibe.core.session.session_migration import migrate_sessions_entrypoint
from vibe.core.skills.manager import SkillManager
from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    InvokeContext,
    ToolError,
    ToolPermission,
    ToolPermissionError,
)
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.mcp import MCPRegistry
from vibe.core.tools.mcp_sampling import MCPSamplingHandler
from vibe.core.trusted_folders import has_agents_md_file
from vibe.core.types import (
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
from vibe.core.utils import (
    TOOL_ERROR_TAG,
    VIBE_STOP_EVENT_TAG,
    CancellationReason,
    get_user_agent,
    get_user_cancellation_message,
    is_user_cancellation_event,
)

try:
    from vibe.core.teleport.teleport import TeleportService as _TeleportService

    _TELEPORT_AVAILABLE = True
except ImportError:
    _TELEPORT_AVAILABLE = False
    _TeleportService = None

if TYPE_CHECKING:
    from vibe.core.teleport.nuage import TeleportSession
    from vibe.core.teleport.teleport import TeleportService
    from vibe.core.teleport.types import TeleportPushResponseEvent, TeleportYieldEvent


class ToolExecutionResponse(StrEnum):
    SKIP = auto()
    EXECUTE = auto()


class ToolDecision(BaseModel):
    verdict: ToolExecutionResponse
    approval_type: ToolPermission
    feedback: str | None = None


MAX_TOOL_TURNS = 200  # Bug fixes rarely need more than 50 turns; 200 is generous ceiling
MAX_API_ERRORS = 5
REPEAT_WARNING_THRESHOLD = 8  # Same exact call 8+ times before warning
REPEAT_FORCE_STOP_THRESHOLD = 25  # Same exact call 25+ times before force-stop

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
        from vibe.core.teleport.nuage import TeleportSession

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
        from vibe.core.teleport.errors import ServiceTeleportError

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

        try:
            should_break_loop = False
            tool_turns = 0
            api_error_count = 0
            repeat_warnings = 0
            has_made_edit = False  # Track if model has used search_replace/write_file
            text_without_action = 0  # Consecutive text responses without tool calls
            bash_count = 0  # Track consecutive bash calls without an edit
            search_replace_failures = 0  # Track failed search_replace attempts
            files_explored: list[str] = []  # Track files read for context state
            context_summary_injected = False
            while not should_break_loop:
                # Loop protection: prevent infinite tool-call loops
                tool_turns += 1
                if tool_turns > MAX_TOOL_TURNS:
                    yield AssistantEvent(
                        content=f"\n\n[Maximum tool call limit ({MAX_TOOL_TURNS}) reached. Stopping.]\n",
                        stopped_by_middleware=True,
                    )
                    return

                result = await self.middleware_pipeline.run_before_turn(
                    self._get_context()
                )
                async for event in self._handle_middleware_result(result):
                    yield event

                if result.action == MiddlewareAction.STOP:
                    return

                self.stats.steps += 1
                user_cancelled = False
                try:
                    async for event in self._perform_llm_turn():
                        if is_user_cancellation_event(event):
                            user_cancelled = True
                        yield event
                        await self._save_messages()
                    # Reset API error count on successful turn
                    api_error_count = 0
                except (RuntimeError, AgentLoopLLMResponseError) as e:
                    api_error_count += 1
                    if api_error_count > MAX_API_ERRORS:
                        yield AssistantEvent(
                            content=f"\n\n[Too many consecutive API errors ({api_error_count}). Last error: {e}. Stopping.]\n",
                            stopped_by_middleware=True,
                        )
                        return
                    # Inject error notice — ALWAYS append to last tool result
                    # vLLM/Mistral rejects user messages after tool messages
                    error_text = f"API error occurred: {e}. Please continue with your task."
                    self._inject_system_note(error_text)
                    continue

                last_message = self.messages[-1]

                # Track files explored, edits made, and bash/failure patterns
                if not has_made_edit:
                    for msg in reversed(self.messages[-5:]):
                        if msg.role == Role.assistant and msg.tool_calls:
                            for tc in msg.tool_calls:
                                if not tc.function:
                                    continue
                                if tc.function.name in ("search_replace", "write_file"):
                                    has_made_edit = True
                                    bash_count = 0  # Reset on successful edit
                                if tc.function.name == "read_file":
                                    try:
                                        args = json.loads(tc.function.arguments or "{}")
                                        path = args.get("path", args.get("file_path", ""))
                                        if path and path not in files_explored:
                                            files_explored.append(path)
                                    except (json.JSONDecodeError, AttributeError):
                                        pass
                                if tc.function.name in ("bash", "run_command"):
                                    bash_count += 1

                    # Track search_replace failures (called but error in result)
                    for msg in reversed(self.messages[-3:]):
                        if msg.role == Role.tool and msg.content:
                            content = msg.content or ""
                            if "search_replace" in content.lower() and "error" in content.lower():
                                search_replace_failures += 1

                # Bash abuse detection: if model keeps using bash instead of
                # search_replace/read_file, redirect it to the proper tools
                if not has_made_edit and bash_count >= 10 and bash_count % 5 == 0:
                    self._inject_system_note(
                        f"STOP using bash. You have run {bash_count} bash commands without making an edit. "
                        "Use search_replace to edit files, not bash. Use read_file to read files, not cat/head. "
                        "Use grep (the tool) to search, not bash grep. "
                        "Call search_replace NOW with your fix."
                    )

                # search_replace keeps failing — suggest re-reading the file
                if search_replace_failures >= 3 and search_replace_failures % 2 == 1:
                    self._inject_system_note(
                        f"Your search_replace has failed {search_replace_failures} times. "
                        "The text you're searching for doesn't match the file. STOP guessing. "
                        "Use read_file with offset/limit to see the EXACT current text, "
                        "then copy it precisely into search_replace old_str."
                    )

                # Context budget warning: after 7 tool turns without an edit,
                # warn that context is being consumed without progress
                if tool_turns == 7 and not has_made_edit and not context_summary_injected:
                    context_summary_injected = True
                    summary = (
                        "CONTEXT BUDGET WARNING: You have used 7 tool calls without making an edit. "
                        "Your context window is filling up — performance degrades as it fills. "
                    )
                    if files_explored:
                        summary += "Files explored: " + ", ".join(files_explored[-5:]) + ". "
                    summary += (
                        "You MUST state your TARGET/FUNCTION/CAUSE/FIX now and use search_replace "
                        "on your next turn. Do NOT continue exploring."
                    )
                    self._inject_system_note(summary)

                should_break_loop = last_message.role != Role.tool

                # If model gives text without tool calls and hasn't edited anything,
                # nudge it to make an edit instead of just describing what to do.
                # Never let the agent exit without at least attempting an edit.
                if should_break_loop and not has_made_edit and tool_turns >= 2:
                    text_without_action += 1
                    # Always continue — don't let the agent exit without editing
                    should_break_loop = False
                    if text_without_action == 1:
                        # Check if model already identified a TARGET in its text
                        last_text = (last_message.content or "").upper()
                        has_target = "TARGET:" in last_text or "FILE:" in last_text
                        if has_target:
                            nudge_text = (
                                "Good — you identified the target. Now proceed to PHASE 2: "
                                "use read_file to read the target function, then search_replace to fix it. "
                                "Do NOT describe the fix — apply it with search_replace."
                            )
                        else:
                            nudge_text = (
                                "You responded with text but did not call any tools. "
                                "Use search_replace NOW to apply your code change. "
                                "If you are unsure of the exact text, use read_file on the target function ONCE, "
                                "then immediately search_replace."
                            )
                    elif text_without_action == 2:
                        nudge_text = (
                            "STOP describing the fix. You MUST call search_replace on your NEXT response. "
                            "No more text-only responses. Act NOW."
                        )
                    elif text_without_action <= 5:
                        nudge_text = (
                            f"WARNING ({text_without_action} text responses without editing): "
                            "You MUST call search_replace or read_file immediately. "
                            "Make your best fix attempt RIGHT NOW even if you are unsure. "
                            "Pick the most likely file and function, and make a minimal edit."
                        )
                    else:
                        # Give up after 5 futile nudges — the model can't/won't edit
                        should_break_loop = True
                        nudge_text = None
                    if nudge_text:
                        self._inject_system_note(nudge_text)
                        logger.info("Model gave text without editing — nudging (attempt %d)", text_without_action)

                # If model has been investigating for too long without making an edit,
                # force it to act (catches grep/read_file loops that don't trigger
                # the text-without-action check because they DO use tools)
                if not should_break_loop and not has_made_edit and tool_turns >= 15:
                    if tool_turns == 15:
                        nudge_text = (
                            "You have spent 15 tool calls investigating without making an edit. "
                            "You MUST use search_replace on your next turn. Pick the most likely target "
                            "file based on what you've seen and make your fix attempt NOW."
                        )
                    elif tool_turns % 5 == 0:
                        nudge_text = (
                            f"WARNING: {tool_turns} tool calls without editing. "
                            "Use search_replace IMMEDIATELY. No more investigation."
                        )
                    else:
                        nudge_text = None
                    if nudge_text:
                        self._inject_system_note(nudge_text)

                # Check for repeated tool calls (loop detection)
                if not should_break_loop:
                    rep = self._check_tool_call_repetition()
                    if rep == "FORCE_STOP":
                        repeat_warnings += 1
                        logger.warning("Detected infinite loop: same tool call %d+ times (warning %d)", REPEAT_FORCE_STOP_THRESHOLD, repeat_warnings)
                        if repeat_warnings >= 12:
                            # True last resort — only stop after many redirects
                            yield AssistantEvent(
                                content="\n\n[Stopping: exhausted all retry attempts.]\n",
                                stopped_by_middleware=True,
                            )
                            return
                        # Redirect instead of kill — force an edit attempt
                        self._inject_system_note(
                            "CRITICAL: You are in an infinite loop. STOP all searching/reading. "
                            "Based on what you have already seen, use search_replace RIGHT NOW to make "
                            "your best fix attempt. If you don't know the exact text, use read_file ONE MORE TIME "
                            "on the specific function, then search_replace immediately.",
                            replace_last_tool=True,
                        )
                        self._prune_repeated_tool_calls()
                    elif rep and rep.startswith("WARNING"):
                        # During investigation (no edit yet), soft-count warnings from
                        # investigation tools — they need room to explore.
                        stuck_tool = ""
                        if "|" in rep:
                            stuck_tool = rep.split("|", 1)[1]
                        is_investigation_warning = (
                            not has_made_edit
                            and stuck_tool in ("grep", "read_file")
                        )
                        if is_investigation_warning:
                            repeat_warnings += 0.3  # Count less — investigation needs room
                        else:
                            repeat_warnings += 1
                        logger.warning("Detected repeated tool calls (warning %.1f, investigation=%s)", repeat_warnings, is_investigation_warning)
                        if repeat_warnings >= 15:
                            yield AssistantEvent(
                                content="\n\n[Stopping: too many repeated actions despite warnings.]\n",
                                stopped_by_middleware=True,
                            )
                            return

                        # On 3rd warning: prune duplicate tool calls from history
                        # This gives the model a "fresh start" with less repetitive context
                        if repeat_warnings == 3:
                            self._prune_repeated_tool_calls()

                        # Extract tool-specific nudge
                        nudge = ""
                        if stuck_tool in ("read_file",):
                            nudge = " You have been READING the same code repeatedly. You already know what it says. Make your EDIT now using search_replace or write_file."
                        elif stuck_tool in ("grep",):
                            nudge = " You have been SEARCHING repeatedly. Pick the most relevant file from your search results and READ it, then make your edit."
                        elif stuck_tool in ("bash", "run_command"):
                            nudge = " You have been running bash commands repeatedly without making progress. STOP testing and make your code edit with search_replace now. If your fix doesn't work, try a DIFFERENT approach."
                        if repeat_warnings >= 4:
                            # Escalated: replace tool result with strong directive
                            warning_text = (
                                "BLOCKED: Your tool call was not executed because you have been repeating the same action. "
                                "You already have all the information you need from previous tool results. "
                                "Your ONLY valid next action is: use search_replace to make your code fix. "
                                "Do NOT read, grep, or bash. Use search_replace NOW."
                            )
                            self._inject_system_note(warning_text, replace_last_tool=True)
                        else:
                            warning_text = (
                                "WARNING: You are repeating the same tool call with the same arguments. "
                                "This is not making progress. Try a DIFFERENT approach, use a different tool, "
                                "or tell the user what's blocking you." + nudge
                            )
                            self._inject_system_note(warning_text)

                if user_cancelled:
                    return

        finally:
            await self._save_messages()

    async def _perform_llm_turn(self) -> AsyncGenerator[BaseEvent, None]:
        if self.enable_streaming:
            async for event in self._stream_assistant_events():
                yield event
        else:
            assistant_event = await self._get_assistant_event()
            if assistant_event.content:
                yield assistant_event

        last_message = self.messages[-1]

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

    async def _process_one_tool_call(
        self, tool_call: ResolvedToolCall
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent]:
        try:
            tool_instance = self.tool_manager.get(tool_call.tool_name)
        except Exception as exc:
            error_msg = f"Error getting tool '{tool_call.tool_name}': {exc}"
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
                error_msg += (
                    "\n\n[RECOVERY: Your search text didn't match the file contents. "
                    "Use read_file with offset/limit to re-read the exact area you want to change, "
                    "then retry search_replace with the EXACT text from read_file output."
                    f"{sr_file_hint}]"
                )
            elif tool_call.tool_name == "search_replace" and "multiple" in str(exc).lower():
                error_msg += (
                    "\n\n[RECOVERY: Your search text matches multiple locations. "
                    "Add more surrounding context lines to old_str to make it unique.]"
                )

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
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent | ToolStreamEvent]:
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

        self.telemetry_client.send_tool_call_finished(
            tool_call=tool_call,
            agent_profile_name=self.agent_profile.name,
            status=status,
            decision=decision,
            result=result,
        )

    def _sanitize_message_ordering(self) -> None:
        """Fix any role ordering violations before sending to vLLM/Mistral.

        vLLM/Mistral rejects:
        - 'user' messages immediately after 'tool' messages
        - 'assistant' as the last message (conflicts with add_generation_prompt)

        This runs as a safety net before every LLM call.
        """
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
        if self.messages[-1].role == Role.assistant:
            self.messages.append(LLMMessage(role=Role.user, content="Continue."))

    async def _chat(self, max_tokens: int | None = None) -> LLMChunk:
        self._sanitize_message_ordering()

        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)

        available_tools = self.format_handler.get_available_tools(self.tool_manager)
        tool_choice = self.format_handler.get_tool_choice()

        try:
            start_time = time.perf_counter()
            result = await self.backend.complete(
                model=active_model,
                messages=self.messages,
                temperature=active_model.temperature,
                tools=available_tools,
                tool_choice=tool_choice,
                extra_headers=self._get_extra_headers(provider),
                max_tokens=max_tokens,
                metadata=self.entrypoint_metadata.model_dump()
                if self.entrypoint_metadata
                else None,
            )
            end_time = time.perf_counter()

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
            async for chunk in self.backend.complete_streaming(
                model=active_model,
                messages=self.messages,
                temperature=active_model.temperature,
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
                sig = hashlib.md5(
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
        if (
            len(sigs) >= REPEAT_WARNING_THRESHOLD
            and all(s == sigs[-1] for s in sigs[-REPEAT_WARNING_THRESHOLD:])
        ):
            return f"WARNING|{last_tool}"

        # Check 2: Same tool called N+ times consecutively with different args
        # Investigation tools (grep, read_file) need more room — typical bug-fix
        # requires 3-5 greps + 3-5 reads before making an edit.
        # Bash gets a higher threshold since it's naturally used more often.
        if last_tool in ("bash", "run_command"):
            same_tool_limit = 12
        elif last_tool in ("grep", "read_file"):
            same_tool_limit = 12  # investigation tools need room to explore
        else:
            same_tool_limit = 8
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

        return None

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
                sig = hashlib.md5("|".join(sorted(call_parts)).encode()).hexdigest()[:16]

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
            empty_assistant_msg = LLMMessage(role=Role.assistant, content="Understood.")
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
