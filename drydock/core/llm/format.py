from __future__ import annotations

import json
import logging
import re as _re
from pathlib import Path
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


_SAFE_PARSE_LOGGER = logging.getLogger(__name__)


def safe_parse_tool_args(raw_json: str | None, tool_name: str = "?") -> dict[str, Any]:
    """Parse a tool-call `arguments` JSON string with Gemma-4 leak/escape
    sanitization fallback.

    The model frequently leaks thinking-token markers INSIDE JSON string
    values — `<|\\Fix`, `<|channel>thought<channel|>`, unpaired `<|...|>`.
    Literal `\\F` (or any `\\X` not in `" \\ / b f n r t u`) is an invalid
    JSON escape and aborts the stdlib decoder, destroying the whole tool
    call. This helper:

    1. Tries straight json.loads.
    2. On failure, strips known leak patterns.
    3. Escapes any remaining orphan backslashes.
    4. Retries; if still bad, tries brace-balance fix.
    5. As last resort, returns {"_parse_error": "..."} so the tool
       validation can surface a clear error rather than an uncaught
       JSONDecodeError.

    Use this at every tool-arg json.loads site.
    """
    raw = (raw_json or "").strip()
    if not raw:
        return {}
    try:
        return _clean_string_values(json.loads(raw))
    except json.JSONDecodeError:
        pass

    _SAFE_PARSE_LOGGER.warning(
        "Malformed tool call JSON for %s: %s", tool_name, raw[:200]
    )

    sanitized = raw
    sanitized = _re.sub(
        r"<\|channel\>.*?(?:<channel\|>|<tool_call\|>)",
        "", sanitized, flags=_re.DOTALL,
    )
    sanitized = _re.sub(
        r"<\|tool_call\>.*?<tool_call\|>", "", sanitized, flags=_re.DOTALL,
    )
    sanitized = _re.sub(r"<\|[^>]{0,200}\|>", "", sanitized)
    sanitized = _re.sub(r"<\|[^>]{0,200}>", "", sanitized)
    sanitized = _re.sub(r"<[^>]{0,200}\|>", "", sanitized)
    sanitized = _re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", sanitized)

    parsed: dict | None = None
    try:
        parsed = json.loads(sanitized)
    except json.JSONDecodeError:
        opens = sanitized.count("{") - sanitized.count("}")
        if opens > 0:
            try:
                parsed = json.loads(sanitized + "}" * opens)
            except json.JSONDecodeError:
                pass
    if parsed is None:
        return {"_parse_error": f"Malformed JSON: {raw[:100]}..."}
    return _clean_string_values(parsed)


def _clean_string_values(obj: Any, field_name: str = "") -> Any:
    r"""Walk a JSON-decoded structure and clean leaked-token residue from
    string values. After json.loads, a tool-arg path like
    `"tool\\_agent/parser.\\py<|\"|>"` becomes the literal Python string
    `tool\_agent/parser.\py<|"|>` — preserves the corruption. Tool
    validation then rejects it and the model retries the same broken
    path forever.

    Observed 2026-04-15 stress session 20260415_041558: 40+ write_file
    calls to `"tool\_agent/parser.\py"<|"|>` in an attractor loop with
    8 distinct signatures (dodging both exact-repeat and 3-variant
    oscillation detectors).

    Cleaning depth depends on the field name (outer JSON key):

    * path-like keys (`path`, `file_path`, `file`, `command`, `cwd`,
      `url`) — aggressive: strip `<|...|>` tokens AND drop orphan
      backslashes before letters (paths should never contain those).
    * content-like keys (`content`, `new_string`, `old_string`,
      `text`, `body`, `description`) — conservative: only strip
      `<|...|>` tokens; preserve `\d` / `\w` / `\n` / `\t` etc. which
      are legitimate in source code, regex patterns, etc.
    * everything else — conservative.
    """
    import re as _re
    PATH_LIKE = {"path", "file_path", "file", "command", "cwd", "url"}
    if isinstance(obj, dict):
        return {k: _clean_string_values(v, field_name=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_string_values(v, field_name=field_name) for v in obj]
    if isinstance(obj, str):
        s = obj
        # Always strip leaked channel/tool_call tokens — never legit content.
        s = _re.sub(r"<\|channel\>.*?(?:<channel\|>|<tool_call\|>)", "", s, flags=_re.DOTALL)
        s = _re.sub(r"<\|tool_call\>.*?<tool_call\|>", "", s, flags=_re.DOTALL)
        s = _re.sub(r"<\|[^>]{0,200}\|>", "", s)
        s = _re.sub(r"<\|[^>]{0,200}>", "", s)
        s = _re.sub(r"<[^>]{0,200}\|>", "", s)
        if field_name in PATH_LIKE:
            # Drop orphan backslashes before letters only on path-like
            # fields. Real paths never have `\d` or `\p`. Model emits
            # these as over-escaped JSON; this normalizes them.
            s = _re.sub(r"\\(?=[A-Za-z_])", "", s)
        return s
    return obj


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
    def __init__(self) -> None:
        # Track per-path truncated-history write_file retries so we can
        # escalate on 2nd+ identical failures (model ignores advisory-only msgs).
        self._truncated_hit_count: dict[str, int] = {}

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
        if content:
            import re as _re
            # Case 1: marked leaks with <|...|> delimiters.
            if "<|channel>" in content or "<|tool_call>" in content:
                content = _re.sub(
                    r"<\|channel\>.*?(?:<channel\|>|<tool_call\|>)",
                    "", content, flags=_re.DOTALL,
                )
                content = _re.sub(
                    r"<\|tool_call\>.*?<tool_call\|>",
                    "", content, flags=_re.DOTALL,
                )
                content = _re.sub(r"<\|[^>]{0,200}\|>", "", content)
                content = _re.sub(r"<\|[^>]{0,200}>", "", content)
                content = _re.sub(r"<[^>]{0,200}\|>", "", content)

            # Case 2: unmarked degenerate fake-tool-call text. Gemma 4
            # sometimes stops emitting real tool_calls and instead writes
            # the tool-call template as plain text, like:
            #   "thought call:write_file{content:class Foo: ..."
            #   "(thought) call:write_file{...}"
            # Drydock parses no tool_calls → nothing runs → user-facing
            # hang. Observed 2026-04-15 stress session 20260415_072556
            # prompts 16-30: every assistant turn was pure fake-tool text.
            # Strip leading "thought"/"(thought)" filler and match the
            # `call:name{...}` shape so the agent loop sees empty content
            # and can fire its recovery nudge.
            #
            # IMPORTANT: `stripped` is for PATTERN MATCHING ONLY. We must
            # NOT replace `content` with `stripped` in the no-match case —
            # that ate leading/trailing spaces from every streaming chunk
            # (issue #1: a Gemma chunk like " received" became "received"
            # so the rendered text had no spaces between words).
            stripped = content.strip()
            # Peel "thought" or "(thought)" prefix (case-insensitive, optional
            # whitespace)
            peeled = _re.sub(
                r"^\s*\(?thought\)?\s*", "", stripped, flags=_re.IGNORECASE,
            )
            if _re.match(r"^call:\w+\s*\{", peeled) or _re.match(r"^\w+\s*\{\s*content\s*:", peeled):
                content = None
            elif _re.match(r"^(call:\w+\{|\w+\{content:)", stripped):
                content = None
            elif (not message.tool_calls
                  and _re.match(
                      r"^[a-z_][a-z0-9_]*\s*\(\s*[a-zA-Z_]\w*\s*=.*\)\s*$",
                      peeled, flags=_re.DOTALL,
                  )):
                # Issue #11: Gemma 4 sometimes emits Python-syntax tool
                # calls as plain text instead of using the OpenAI
                # tool_calls protocol, e.g.
                #   `task(task="Explore project", agent="explore")`
                # Nothing runs and the user sees a dead-looking response.
                # Match `name(arg=...)` shape covering the entire content
                # (DOTALL for multi-line strings inside args) and only
                # when there are no real tool_calls. Suppress so the
                # agent loop's empty-content recovery nudge fires.
                content = None
            elif (not message.tool_calls
                  and (_re.match(r"^thought\s*/", stripped)
                       or _re.match(r"^thought\s*\n", stripped))):
                # Narrow Gemma-4 thinking-channel leak: content begins
                # with bare "thought" followed by `/` or newline (the
                # exact token separator shape Gemma emits when it
                # streams thinking as content). Observed v2.6.95 stress
                # session 20260415_093914 prompts 47-60. This DOESN'T
                # match legitimate "Thought: ..." prose or "Thoughts on
                # this..." — only the bare-word leak with `/` or `\n`.
                content = None
            else:
                # Preserve original whitespace. Only collapse to None when
                # the chunk is genuinely empty after stripping (so we don't
                # ship pure-whitespace chunks to downstream consumers).
                if not stripped:
                    content = None
                # else: leave `content` as-is (with leading/trailing spaces)

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
            args = safe_parse_tool_args(raw_json, tool_name=function_call.name)

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

            # Detect when model copied truncated-history args (our truncation
            # code replaces large tool-call args with {_truncated, _original_bytes,
            # path}).  Return a clear advisory instead of a pydantic traceback so
            # the model re-does the call with real arguments rather than retrying
            # the truncated form.
            if (isinstance(parsed_call.raw_args, dict)
                    and parsed_call.raw_args.get("_truncated")):
                path_hint = (parsed_call.raw_args.get("path")
                             or parsed_call.raw_args.get("file_path")
                             or "")
                # Track retry count per path to escalate on 2nd+ offense.
                hit_key = path_hint or "<unknown>"
                hit_count = self._truncated_hit_count.get(hit_key, 0) + 1
                self._truncated_hit_count[hit_key] = hit_count
                escalate = hit_count >= 2

                # Auto-embed the current file content so the model can
                # rewrite immediately without an extra read_file round-trip.
                file_embed = ""
                if path_hint:
                    try:
                        p = Path(path_hint)
                        if p.exists() and p.is_file():
                            raw = p.read_text(errors="replace")
                            lines = raw.splitlines()
                            max_lines = 120
                            if len(lines) <= max_lines:
                                file_embed = (
                                    f"\n\nCurrent file content of `{path_hint}`:\n"
                                    f"```\n{raw}\n```\n"
                                    f"Rewrite it now using write_file with the correct content."
                                )
                            else:
                                snippet = "\n".join(lines[:max_lines])
                                file_embed = (
                                    f"\n\nCurrent file content of `{path_hint}` "
                                    f"(first {max_lines} of {len(lines)} lines):\n"
                                    f"```\n{snippet}\n```\n"
                                    f"Use read_file to see the rest, then rewrite with write_file."
                                )
                    except Exception:
                        pass
                if not file_embed and path_hint:
                    file_embed = f" Re-read `{path_hint}` with read_file first."

                if escalate:
                    # Model is stuck in a truncated-template retry loop.
                    # Show project files and give a concrete directive.
                    try:
                        py_files = sorted(
                            str(p2.relative_to(Path.cwd()))
                            for p2 in Path.cwd().rglob("*.py")
                            if "__pycache__" not in str(p2) and ".git" not in str(p2)
                        )[:20]
                        dir_listing = "\n".join(f"  {f}" for f in py_files)
                    except Exception:
                        dir_listing = "  (could not list files)"
                    escalation_suffix = (
                        f"\n\n[REPEATED FAILURE #{hit_count}: your write_file call "
                        f"keeps using a stale truncated template for '{path_hint}'. "
                        f"You MUST type fresh content — do NOT copy from history. "
                        f"Current .py files in project:\n{dir_listing}\n"
                        f"Use read_file on the target file, then call write_file "
                        f"with fully typed content, OR use search_replace to make "
                        f"targeted edits without rewriting the whole file.]"
                    )
                else:
                    escalation_suffix = ""

                failed_calls.append(
                    FailedToolCall(
                        tool_name=parsed_call.tool_name,
                        call_id=parsed_call.call_id,
                        error=(
                            f"your call used a "
                            f"truncated history entry as a template (it contained "
                            f"'_truncated'/'_original_bytes' instead of real "
                            f"arguments).{file_embed} Provide the full required "
                            f"arguments.{escalation_suffix}"
                        ),
                    )
                )
                continue

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
                error_str = str(e)
                if (
                    parsed_call.tool_name == "write_file"
                    and "path" in error_str
                    and "Field required" in error_str
                ):
                    # Try to infer path from the first comment line of content.
                    # Gemma 4 frequently calls write_file(content="...") without path.
                    content_val = parsed_call.raw_args.get("content", "")
                    inferred_path: str | None = None
                    if content_val:
                        for _line in content_val.splitlines()[:5]:
                            _line = _line.strip()
                            _m = _re.match(
                                r'^#\s+([\w./+-]+\.(?:py|js|ts|json|yaml|yml|toml|md|txt|sh|cfg|ini))\s*$',
                                _line,
                            )
                            if _m:
                                inferred_path = _m.group(1)
                                break
                    if inferred_path:
                        try:
                            _new_args = dict(parsed_call.raw_args)
                            _new_args["path"] = inferred_path
                            validated_args = args_model.model_validate(_new_args)
                            resolved_calls.append(
                                ResolvedToolCall(
                                    tool_name=parsed_call.tool_name,
                                    tool_class=tool_class,
                                    validated_args=validated_args,
                                    call_id=parsed_call.call_id,
                                )
                            )
                            continue
                        except ValidationError:
                            pass
                    # List project .py files as hints so the model can pick the right one
                    _candidates: list[str] = []
                    try:
                        _candidates = [
                            str(p.relative_to(Path.cwd()))
                            for p in sorted(Path.cwd().rglob("*.py"))
                            if "__pycache__" not in str(p) and ".git" not in str(p)
                        ][:12]
                    except Exception:
                        pass
                    _hint = (
                        f"Project .py files: {', '.join(_candidates)}"
                        if _candidates
                        else "Use read_file or glob to list available files."
                    )
                    error_msg = (
                        "missing required `path` parameter. "
                        "You must pass BOTH `path` AND `content` as separate arguments: "
                        'write_file(path="pkg/file.py", content="..."). '
                        f"Do NOT omit `path`. {_hint}"
                    )
                else:
                    error_msg = f"Invalid arguments: {e}"
                failed_calls.append(
                    FailedToolCall(
                        tool_name=parsed_call.tool_name,
                        call_id=parsed_call.call_id,
                        error=error_msg,
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
