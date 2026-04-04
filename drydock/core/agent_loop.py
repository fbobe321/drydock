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
        await self._auto_route_task(user_msg)

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
            files_modified: list[str] = []  # Track files edited for blast radius
            context_summary_injected = False
            self._temp_override: float | None = None  # Temperature bump on loop detection
            self._failed_approaches: list[str] = []  # Persistent failed approach tracker
            while not should_break_loop:
                # Loop protection: prevent infinite tool-call loops
                tool_turns += 1
                if tool_turns > MAX_TOOL_TURNS:
                    yield AssistantEvent(
                        content=f"\n\n[Maximum tool call limit ({MAX_TOOL_TURNS}) reached. Stopping.]\n",
                        stopped_by_middleware=True,
                    )
                    return

                # Progressive budget warnings (visible to user via tool results)
                if tool_turns in (50, 100, 150):
                    self._inject_system_note(
                        f"You have used {tool_turns}/{MAX_TOOL_TURNS} tool calls. "
                        "Wrap up your current task. If you are stuck in a loop, "
                        "stop and ask the user for clarification."
                    )

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
                    force_stopped = False
                    async for event in self._perform_llm_turn():
                        if is_user_cancellation_event(event):
                            user_cancelled = True
                        if isinstance(event, AssistantEvent) and event.stopped_by_middleware:
                            force_stopped = True
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
                        # Context limit or malformed request — aggressively compact
                        try:
                            compacted = 0
                            for i, msg in enumerate(self.messages):
                                if i >= len(self.messages) - 4:
                                    break  # Keep last 4 messages
                                if msg.role == Role.tool and hasattr(msg, 'content'):
                                    content = str(msg.content) if msg.content else ""
                                    if len(content) > 200:
                                        msg.content = content[:100] + "\n[truncated]\n" + content[-50:]
                                        compacted += 1
                                elif msg.role == Role.assistant and hasattr(msg, 'content'):
                                    content = str(msg.content) if msg.content else ""
                                    if len(content) > 500:
                                        msg.content = content[:200] + "\n[truncated]"
                                        compacted += 1
                            logger.info("Emergency compaction: truncated %d messages", compacted)
                        except Exception:
                            pass
                        error_text = "Context compacted due to API error. Continue with your task."
                    else:
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
                                    first_edit = not has_made_edit
                                    has_made_edit = True
                                    bash_count = 0  # Reset on successful edit
                                    # Reset circuit breaker — the world changed after an edit,
                                    # so previously-failing commands may now succeed
                                    self._tool_call_history.clear()
                                    self._consecutive_circuit_breaker_fires = 0
                                    # Track blast radius
                                    try:
                                        edit_args = json.loads(tc.function.arguments or "{}")
                                        edit_path = edit_args.get("file_path", edit_args.get("path", ""))
                                        if edit_path and edit_path not in files_modified:
                                            files_modified.append(edit_path)
                                    except (json.JSONDecodeError, AttributeError):
                                        edit_path = ""

                                    # AUTO-FIX: Fix packaging issues the model gets wrong
                                    if edit_path and edit_path.endswith(".py"):
                                        self._auto_fix_package(edit_path)

                                    # Auto-run diagnostics on edited Python files
                                    if edit_path and edit_path.endswith(".py"):
                                        try:
                                            import asyncio
                                            import subprocess
                                            diag_result = subprocess.run(
                                                ["python3", "-c", f"import ast; ast.parse(open('{edit_path}').read()); print('OK')"],
                                                capture_output=True, text=True, timeout=5,
                                            )
                                            if diag_result.returncode != 0:
                                                self._inject_system_note(
                                                    f"SYNTAX ERROR in {edit_path}: {diag_result.stderr.strip()[:200]}. "
                                                    f"Fix the syntax error before continuing."
                                                )
                                        except Exception:
                                            pass

                                    # After first edit: prompt to check related files
                                    if first_edit and edit_path:
                                        self._inject_system_note(
                                            f"Good — you edited {edit_path}. Now check: "
                                            f"does this bug have a RELATED file that also needs changes? "
                                            f"Common patterns: if you edited a model, check the serializer/migration. "
                                            f"If you edited a base class, check subclasses. "
                                            f"If you edited a util, check callers. "
                                            f"Use grep to search for imports of the function/class you changed."
                                        )
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

                # Blast radius check: warn when touching 5+ files
                if len(files_modified) >= 5 and len(files_modified) % 5 == 0:
                    self._inject_system_note(
                        f"BLAST RADIUS WARNING: You have modified {len(files_modified)} files. "
                        f"Files: {', '.join(files_modified[-5:])}. "
                        f"This is a large change. Are you sure all these edits are needed? "
                        f"Consider stopping to verify your approach."
                    )

                # Post-edit bash loop: model made edits but now loops on bash
                # trying to test code that keeps failing
                if has_made_edit and bash_count >= 3:
                    # Count consecutive bash calls at end of tool history
                    consecutive_bash = 0
                    for tc_name in reversed(self._recent_tool_names()):
                        if tc_name in ("bash", "run_command"):
                            consecutive_bash += 1
                        else:
                            break
                    if consecutive_bash >= 3:
                        self._inject_system_note(
                            "You have run 3+ bash commands in a row. "
                            "If your project is WORKING, you are DONE — tell the user the project "
                            "is complete and what commands they can use. "
                            "If it is FAILING, STOP running bash and use read_file + search_replace "
                            "to fix the code. Do NOT keep running the same test."
                        )

                # Detect bash file creation (touch, echo >, cat <<)
                for msg in reversed(self.messages[-3:]):
                    if msg.role == Role.assistant and msg.tool_calls:
                        for tc in msg.tool_calls:
                            if tc.function and tc.function.name in ("bash", "run_command"):
                                cmd = ""
                                try:
                                    cmd = json.loads(tc.function.arguments or "{}").get("command", "")
                                except (json.JSONDecodeError, AttributeError):
                                    pass
                                if any(kw in cmd for kw in ("touch ", "echo ", "cat <<", "printf ", "> ")):
                                    self._inject_system_note(
                                        f"Do NOT use bash to create files. Use write_file instead. "
                                        f"bash touch/echo/cat does not create proper code files."
                                    )

                # Bash abuse detection: model uses bash instead of proper tools
                if not has_made_edit and bash_count >= 3:
                    if bash_count == 3:
                        self._inject_system_note(
                            "You have run 3 bash commands without creating or editing any files. "
                            "STOP running bash. Start CREATING files with write_file or "
                            "EDITING files with search_replace. bash does not create code."
                        )
                    elif bash_count == 6:
                        self._inject_system_note(
                            "WARNING: 6 bash commands, ZERO file edits. You are stuck in a loop. "
                            "Your NEXT action MUST be write_file to create a file or "
                            "search_replace to edit one. Do NOT run any more bash commands."
                        )
                    elif bash_count >= 10 and bash_count % 5 == 0:
                        # Nudge every 5 bash calls, never stop
                        self._inject_system_note(
                            "You have run many bash commands. Consider using write_file "
                            "or search_replace to create/edit files."
                        )

                # 3-Strike Rule: search_replace keeps failing
                if search_replace_failures >= 3:
                    if search_replace_failures == 3:
                        self._inject_system_note(
                            f"3-STRIKE RULE: Your search_replace has failed {search_replace_failures} times. "
                            "STOP trying the same approach. "
                            "Either: (1) use read_file to get the EXACT text first, "
                            "(2) try a completely different file, or "
                            "(3) ask the user for help with /consult."
                        )
                    elif search_replace_failures % 3 == 0:
                        self._inject_system_note(
                            f"STOP: {search_replace_failures} failed edits. You are stuck. Ask the user for help."
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

                # Handle empty response (no content AND no tools) — retry
                if (should_break_loop and last_message.role == Role.assistant
                        and not last_message.content and not last_message.tool_calls):
                    empty_response_count = getattr(self, '_empty_responses', 0) + 1
                    self._empty_responses = empty_response_count
                    if empty_response_count <= 3:
                        should_break_loop = False
                        self._inject_system_note(
                            "Your response was empty. You MUST call a tool. "
                            "Start by reading the relevant file with read_file, "
                            "or run the failing command with bash to see the error."
                        )
                        logger.warning("Empty model response — nudging (attempt %d)", empty_response_count)
                        continue
                    # After 3 empty responses, let it exit

                # If model gives text without tool calls and hasn't edited anything,
                # nudge it to make an edit instead of just describing what to do.
                # Never let the agent exit without at least attempting an edit.
                if should_break_loop and not has_made_edit and tool_turns >= 1:
                    text_without_action += 1
                    # Always continue — don't let the agent exit without editing
                    should_break_loop = False

                    # Detect model claiming task is done without having edited anything
                    last_text_lower = (last_message.content or "").lower()
                    task_complete_phrases = [
                        "task completed", "task complete", "task is complete",
                        "task is done", "task has been completed",
                        "changes have been applied", "fix has been applied",
                        "the fix is complete", "issue is resolved",
                    ]
                    claims_complete = any(p in last_text_lower for p in task_complete_phrases)

                    if claims_complete:
                        nudge_text = (
                            "You said the task is complete but NO code was actually changed. "
                            "Your search_replace call either failed or was never made. "
                            "Use read_file to read the target file, then use search_replace "
                            "to make your edit. Do NOT say the task is done until search_replace succeeds."
                        )
                    elif text_without_action == 1:
                        last_text = (last_message.content or "").lower()
                        # Detect model asking for confirmation instead of acting
                        is_asking = any(kw in last_text for kw in (
                            "standing by", "waiting for", "please provide",
                            "what would you like", "how can i help",
                            "what should i", "do you want me to",
                        ))
                        if is_asking:
                            nudge_text = (
                                "DO NOT ask for confirmation. Act NOW. "
                                "If there is a PRD.md, implement it with write_file. "
                                "If there is code, read it with read_file and fix bugs with search_replace. "
                                "Call a tool on your next response."
                            )
                        elif "TARGET:" in last_text.upper() or "FILE:" in last_text.upper():
                            nudge_text = (
                                "Good — you identified the target. Now use read_file then search_replace."
                            )
                        else:
                            nudge_text = (
                                "You responded with text but did not call any tools. "
                                "Call write_file, read_file, or search_replace NOW."
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
                        # Keep nudging — never give up
                        nudge_text = (
                            "You MUST call a tool. Use grep to find the file, "
                            "read_file to read it, then search_replace to fix."
                        )
                    if nudge_text:
                        # Use _inject_system_note (appends to last tool/user message)
                        # User messages cause 'tool after user' ordering violations
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

                # Loop guidance — retrospection, NEVER stop
                if not should_break_loop:
                    rep = self._check_tool_call_repetition()
                    if rep == "FORCE_STOP" or (rep and rep.startswith("WARNING")):
                        # Prune duplicate tool calls to give model fresh context
                        self._prune_repeated_tool_calls()

                        # Temperature bump: force model to explore different paths
                        self._temp_override = 0.5

                        # Track failed approach for accumulator
                        self._record_failed_approach()

                        # RETROSPECTION: Summarize recent actions and let the model
                        # decide its own next step — no hardcoded nudges
                        retro = self._build_retrospection()
                        if retro:
                            # Include failed approaches if any
                            if self._failed_approaches:
                                approaches = "\n".join(f"  - {a}" for a in self._failed_approaches[-5:])
                                retro += f"\n\nApproaches already tried (don't repeat these):\n{approaches}"
                            self._inject_system_note(retro)
                    else:
                        # Loop broken — reset temperature to normal
                        self._temp_override = None

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
        """Disabled. Loop detection handles repetition.

        The circuit breaker consistently caused more harm than good —
        blocking valid retries after the model fixed code.
        _check_tool_call_repetition() catches actual infinite loops.
        """
        return None

    def _circuit_breaker_check_FULL(self, tool_call: ResolvedToolCall) -> str | None:
        """Block exact-duplicate tool calls. Returns cached result or None.

        Thresholds:
        - Read-only tools (grep, read_file, ls, pwd, git status): block after 4
        - Write/edit tools (search_replace, write_file): block after 2
        - Other (bash with commands): block after 3
        """
        args_str = json.dumps(tool_call.args_dict, sort_keys=True, default=str)
        sig = hashlib.md5(
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
        sig = hashlib.md5(
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
            # Use temperature override if set (loop detection bumps it)
            temp = getattr(self, '_temp_override', None) or active_model.temperature
            result = await self.backend.complete(
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
            # Use temperature override if set (loop detection bumps it)
            temp = getattr(self, '_temp_override', None) or active_model.temperature
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

        return None

    async def _auto_route_task(self, user_msg: str) -> None:
        """Lightweight auto-context: list project files and key docs.

        Kept minimal to avoid bloating context — just filenames, no content.
        """
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
