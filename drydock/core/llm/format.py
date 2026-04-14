from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from drydock.core.tools.base import BaseTool
from drydock.core.types import (
    AvailableFunction,
    AvailableTool,
    LLMMessage,
    Role,
    StrToolChoice,
)

if TYPE_CHECKING:
    from drydock.core.tools.manager import ToolManager


class ParsedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_name: str
    raw_args: dict[str, Any]
    call_id: str = ""


class ResolvedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    tool_name: str
    tool_class: type[BaseTool]
    validated_args: BaseModel
    call_id: str = ""

    @property
    def args_dict(self) -> dict[str, Any]:
        return self.validated_args.model_dump()


class FailedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_name: str
    call_id: str
    error: str


class ParsedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_calls: list[ParsedToolCall]


class ResolvedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_calls: list[ResolvedToolCall]
    failed_calls: list[FailedToolCall] = Field(default_factory=list)


class APIToolFormatHandler:
    @property
    def name(self) -> str:
        return "api"

    def get_available_tools(self, tool_manager: ToolManager) -> list[AvailableTool]:
        return [
            AvailableTool(
                function=AvailableFunction(
                    name=tool_class.get_name(),
                    description=tool_class.description,
                    parameters=tool_class.get_parameters(),
                )
            )
            for tool_class in tool_manager.available_tools.values()
        ]

    def get_tool_choice(self) -> StrToolChoice | AvailableTool:
        return "auto"

    def process_api_response_message(self, message: Any) -> LLMMessage:
        # Strip ALL Gemma 4 leaked channel/tool-call tokens from content.
        # The model emits various formats:
        #   <|channel>thought...<channel|>  (thinking tokens)
        #   <|channel>call:tool_name{...}<tool_call|>  (malformed tool calls)
        #   <|channel><|channel>list_mcp_resources{...}<tool_call|>  (double channel)
        #   <|tool_call>call:write_file{content:<|"|>import ...  (bare tool_call,
        #       observed in color_converter session 20260414_121725 after a
        #       syntax-thrash BLOCK — model panicked, emitted this as its FINAL
        #       assistant turn with no real tool call; shakedown interpreted the
        #       text-only response as completion and ended the session.)
        # Keeping them wastes context, confuses subsequent turns, and can
        # fake-signal "done" to harnesses that watch for text-only messages.
        content = message.content
        if content and ("<|channel>" in content or "<|tool_call>" in content):
            import re as _re
            # Strip <|channel>...<channel|> or <|channel>...<tool_call|> blocks
            content = _re.sub(
                r"<\|channel\>.*?(?:<channel\|>|<tool_call\|>)",
                "",
                content,
                flags=_re.DOTALL,
            )
            # Strip bare <|tool_call>call:...<tool_call|> blocks (no channel
            # prefix). Unterminated variants (stream cut off mid-emission)
            # are stripped by the catch-all below.
            content = _re.sub(
                r"<\|tool_call\>.*?<tool_call\|>",
                "",
                content,
                flags=_re.DOTALL,
            )
            # Final catch-all: any dangling <|...|> or <...|> tokens that
            # didn't form a complete pair (stream truncation).
            content = _re.sub(r"<\|[^>]{0,200}\|>", "", content)
            content = _re.sub(r"<\|[^>]{0,200}>", "", content)
            content = _re.sub(r"<[^>]{0,200}\|>", "", content)
            # If remaining content is just tool-call-leak residue (starts
            # with `call:toolname{` after stripping), nuke it. Otherwise
            # harness watchers treat the garbage text as a completion
            # signal. agent_loop's empty-content nudge will ask the model
            # to continue with a real tool call.
            stripped = content.strip()
            if _re.match(r"^(call:\w+\{|\w+\{content:)", stripped):
                content = None
            else:
                content = stripped or None

        clean_message = {
            "role": message.role,
            "content": content,
            "reasoning_content": getattr(message, "reasoning_content", None),
            "reasoning_signature": getattr(message, "reasoning_signature", None),
        }

        if message.tool_calls:
            clean_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "index": tc.index,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        return LLMMessage.model_validate(clean_message)

    def parse_message(self, message: LLMMessage) -> ParsedMessage:
        tool_calls = []

        api_tool_calls = message.tool_calls or []
        for tc in api_tool_calls:
            if not (function_call := tc.function):
                continue
            raw_json = function_call.arguments or "{}"
            # Debug: log raw args for diagnosis
            import logging
            _fmt_logger = logging.getLogger(__name__)
            _fmt_logger.info(
                "Tool call %s: args_len=%d, first_100=%s",
                function_call.name, len(raw_json), raw_json[:100]
            )
            try:
                args = json.loads(raw_json)
            except json.JSONDecodeError:
                # Don't silently use {} — log the malformed args and skip
                import logging
                logging.getLogger(__name__).warning(
                    "Malformed tool call JSON for %s: %s",
                    function_call.name,
                    (function_call.arguments or "")[:200],
                )
                # Try to salvage partial JSON by closing brackets
                raw = (function_call.arguments or "").strip()
                if raw:
                    # Count open/close braces and try to fix
                    opens = raw.count('{') - raw.count('}')
                    raw_fixed = raw + '}' * max(0, opens)
                    try:
                        args = json.loads(raw_fixed)
                    except json.JSONDecodeError:
                        args = {"_parse_error": f"Malformed JSON: {raw[:100]}..."}
                else:
                    args = {}

            tool_calls.append(
                ParsedToolCall(
                    tool_name=function_call.name or "",
                    raw_args=args,
                    call_id=tc.id or "",
                )
            )

        return ParsedMessage(tool_calls=tool_calls)

    def resolve_tool_calls(
        self, parsed: ParsedMessage, tool_manager: ToolManager
    ) -> ResolvedMessage:
        resolved_calls = []
        failed_calls = []

        active_tools = tool_manager.available_tools

        # Tools the model hallucinates that should be silently ignored
        # rather than generating a visible error that confuses the user.
        _IGNORE_TOOLS = {
            "exit_plan_mode", "enter_plan_mode", "plan_mode",
            # Gemma 4 confabulated tools — observed in user sessions and
            # mini_db fresh build (called ralph_repo_index 5 times).
            "ralph_repo_index", "repo_index", "index_repo",
            "list_mcp_resources", "list_resources", "search_resources",
        }

        for parsed_call in parsed.tool_calls:
            tool_class = active_tools.get(parsed_call.tool_name)
            if not tool_class:
                # Silently drop known hallucinated tools instead of
                # showing an error in the TUI
                if parsed_call.tool_name in _IGNORE_TOOLS:
                    continue
                failed_calls.append(
                    FailedToolCall(
                        tool_name=parsed_call.tool_name,
                        call_id=parsed_call.call_id,
                        error=(
                            f"Unknown tool '{parsed_call.tool_name}'. "
                            f"Available tools: {', '.join(sorted(active_tools.keys())[:10])}. "
                            f"Use one of these exact names."
                        ),
                    )
                )
                continue

            args_model, _ = tool_class._get_tool_args_results()
            try:
                validated_args = args_model.model_validate(parsed_call.raw_args)
                resolved_calls.append(
                    ResolvedToolCall(
                        tool_name=parsed_call.tool_name,
                        tool_class=tool_class,
                        validated_args=validated_args,
                        call_id=parsed_call.call_id,
                    )
                )
            except ValidationError as e:
                failed_calls.append(
                    FailedToolCall(
                        tool_name=parsed_call.tool_name,
                        call_id=parsed_call.call_id,
                        error=f"Invalid arguments: {e}",
                    )
                )

        return ResolvedMessage(tool_calls=resolved_calls, failed_calls=failed_calls)

    def create_tool_response_message(
        self, tool_call: ResolvedToolCall, result_text: str
    ) -> LLMMessage:
        return LLMMessage(
            role=Role.tool,
            tool_call_id=tool_call.call_id,
            name=tool_call.tool_name,
            content=result_text,
        )

    def create_failed_tool_response_message(
        self, failed: FailedToolCall, error_content: str
    ) -> LLMMessage:
        return LLMMessage(
            role=Role.tool,
            tool_call_id=failed.call_id,
            name=failed.tool_name,
            content=error_content,
        )
