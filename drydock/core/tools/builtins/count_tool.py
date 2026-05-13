"""Count tool — exact substring / regex / line / token counts.

The companion to the math tool: transformers can't count reliably (they
estimate token positions and overshoot/undershoot on "how many X are in
this text"). This tool fixes it by deferring to deterministic stdlib code.

Use INSTEAD of estimating "how many" anything. The model just calls and
gets the exact number.

Modes:
- substring      — case-sensitive substring count (default)
- substring_ci   — case-insensitive substring count
- regex          — regex match count (uses re.findall)
- lines          — number of lines (newline-separated)
- words          — whitespace-separated word count
- chars          — total character count
- bytes          — UTF-8 byte length

Source can be:
- `text` arg directly
- `path` arg pointing at a file (read up to 10 MB)

Examples:

    count(pattern="r", text="strawberry")              # 3
    count(pattern="def ", path="/data3/drydock/drydock/core/agent_loop.py", mode="substring")
    count(pattern="^class\\s", path="...", mode="regex")
    count(mode="lines", path="...")
    count(mode="words", text="hello world hello")      # 3
"""
from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from drydock.core.types import ToolResultEvent


_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB

CountMode = Literal[
    "substring", "substring_ci", "regex", "lines", "words", "chars", "bytes",
]


class CountArgs(BaseModel):
    mode: CountMode = Field(
        default="substring",
        description=(
            "How to count. `substring` = case-sensitive substring (needs "
            "`pattern`). `substring_ci` = case-insensitive. `regex` = "
            "Python re.findall (needs `pattern`). `lines` / `words` / "
            "`chars` / `bytes` = pattern-free."
        ),
    )
    pattern: str = Field(
        default="",
        description=(
            "Required for substring / substring_ci / regex. Ignored otherwise."
        ),
    )
    text: str = Field(
        default="",
        description=(
            "Inline text to count in. Provide either `text` OR `path`, not both."
        ),
    )
    path: str = Field(
        default="",
        description=(
            "Path to a file to count in. Capped at 10 MB. Use either `text` "
            "OR `path`."
        ),
    )


class CountResult(BaseModel):
    ok: bool
    count: int = 0
    mode: str = ""
    source: str = ""    # "text" or the file path
    error: str = ""


class CountConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


def _resolve_text(args: CountArgs) -> tuple[str, str]:
    """Returns (text, source_label). Raises ValueError on bad input.

    Empty text + empty path is treated as the empty string source — valid
    for pattern-free modes like `lines` (returns 0). Substring / regex
    modes still error out via `_do_count` if the pattern is empty.
    """
    if args.text and args.path:
        raise ValueError("provide either `text` or `path`, not both")
    if args.path:
        p = Path(args.path).expanduser()
        if not p.is_file():
            raise ValueError(f"file not found: {p}")
        size = p.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise ValueError(
                f"file too large ({size} bytes > {_MAX_FILE_BYTES})"
            )
        return p.read_text(errors="replace"), str(p)
    return args.text, "text"


def _do_count(text: str, args: CountArgs) -> int:
    if args.mode == "substring":
        if not args.pattern:
            raise ValueError("substring mode requires `pattern`")
        return text.count(args.pattern)
    if args.mode == "substring_ci":
        if not args.pattern:
            raise ValueError("substring_ci mode requires `pattern`")
        return text.lower().count(args.pattern.lower())
    if args.mode == "regex":
        if not args.pattern:
            raise ValueError("regex mode requires `pattern`")
        try:
            return len(re.findall(args.pattern, text))
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
    if args.mode == "lines":
        # If text ends without a newline, it still counts as one line.
        if not text:
            return 0
        return text.count("\n") + (0 if text.endswith("\n") else 1)
    if args.mode == "words":
        return len(text.split())
    if args.mode == "chars":
        return len(text)
    if args.mode == "bytes":
        return len(text.encode("utf-8"))
    raise ValueError(f"unknown mode: {args.mode}")


class Count(
    BaseTool[CountArgs, CountResult, CountConfig, BaseToolState],
    ToolUIData[CountArgs, CountResult],
):
    description: ClassVar[str] = (
        "Exact count of substrings, regex matches, lines, words, chars, "
        "or bytes in a string OR file. Use INSTEAD of estimating 'how "
        "many X are in this text' — transformers count poorly. Modes: "
        "substring (default), substring_ci, regex, lines, words, chars, "
        "bytes. Source: pass `text=...` OR `path=...` (file capped at "
        "10 MB). Examples: count(pattern='r', text='strawberry') -> 3; "
        "count(pattern='^def ', path='foo.py', mode='regex'); "
        "count(mode='lines', path='foo.txt')."
    )

    @classmethod
    def format_call_display(cls, args: CountArgs) -> ToolCallDisplay:
        if args.path:
            target = args.path.split("/")[-1] if "/" in args.path else args.path
            target = f"file:{target}"
        else:
            preview = args.text[:30].replace("\n", " ")
            target = f'text:"{preview}..."' if len(args.text) > 30 else f'text:"{preview}"'
        if args.mode in ("substring", "substring_ci", "regex") and args.pattern:
            return ToolCallDisplay(summary=f"count {args.mode} {args.pattern!r} in {target}")
        return ToolCallDisplay(summary=f"count {args.mode} in {target}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, CountResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"count: {event.result.error[:80]}"
                )
            return ToolResultDisplay(
                success=True,
                message=f"= {event.result.count} ({event.result.mode})",
            )
        return ToolResultDisplay(success=True, message="count complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Counting"

    def resolve_permission(self, args: CountArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: CountArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | CountResult, None]:
        try:
            text, source = _resolve_text(args)
            n = _do_count(text, args)
        except ValueError as e:
            yield CountResult(ok=False, error=str(e), mode=args.mode)
            return
        except OSError as e:
            yield CountResult(
                ok=False, error=f"OSError: {e}", mode=args.mode
            )
            return
        yield CountResult(ok=True, count=n, mode=args.mode, source=source)
