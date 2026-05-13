"""Verify tool — operationalizes "Loop until verified" from the 4 core principles.

After making a change or claiming success, the model can call:

    verify(criterion="all tests pass",
           command="pytest -q",
           expect="passed",
           expect_mode="contains")

The tool runs the command, checks the output against the expectation,
and returns a structured pass/fail. The model then knows whether to
declare done or iterate.

This catches the "confident-but-wrong" failure mode the surprise
scorer in the Curiosity Layer also targets — but proactively, before
the model claims success in chat. Cheap deterministic loop, no extra
LLM calls.

Modes for matching `expect` against the command's output:
- contains          (default) — expect substring appears in output
- not_contains      — expect substring is ABSENT from output
- regex             — re.search match
- equals            — exact string equality (after strip)
- exit_code         — pass `expect` as the integer exit code to require
- file_exists       — expect a file path; pass if it exists (no command needed)
- file_contains     — expect "PATH::SUBSTRING"; pass if the file contains substring

The `command` runs via /bin/bash -lc in the cwd of the agent. 30s
timeout default, max 300s. Output capped at 64 KB.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
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


_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 300


VerifyMode = Literal[
    "contains", "not_contains", "regex", "equals",
    "exit_code", "file_exists", "file_contains",
]


class VerifyArgs(BaseModel):
    criterion: str = Field(
        description=(
            "Plain-English description of what 'success' means here. "
            "Recorded in the result so the operator can grep for verified "
            "claims later. Examples: 'all tests pass', 'no flake8 "
            "warnings', 'README contains the new section'."
        )
    )
    command: str = Field(
        default="",
        description=(
            "Shell command to run. Required for contains / not_contains / "
            "regex / equals / exit_code modes. Ignored for file_* modes."
        ),
    )
    expect: str = Field(
        default="",
        description=(
            "What we're looking for in the output. Interpretation depends "
            "on `expect_mode`. For exit_code: the integer to require. For "
            "file_contains: 'PATH::SUBSTRING'. Otherwise: the substring / "
            "regex / exact-match string."
        ),
    )
    expect_mode: VerifyMode = Field(
        default="contains",
        description="How to compare command output (or path) against `expect`.",
    )
    timeout: int = Field(
        default=_DEFAULT_TIMEOUT,
        ge=1,
        le=_MAX_TIMEOUT,
        description="Per-command timeout in seconds (max 300).",
    )
    cwd: str = Field(
        default="",
        description="Working directory. Defaults to the agent's cwd.",
    )


class VerifyResult(BaseModel):
    ok: bool
    criterion: str
    expect_mode: str
    passed: bool
    exit_code: int | None = None
    output: str = ""        # truncated stdout/stderr
    error: str = ""
    reason: str = ""        # one-line why pass/fail


class VerifyConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


def _truncate(text: str, n: int = _MAX_OUTPUT_BYTES) -> str:
    b = text.encode("utf-8", errors="replace")
    if len(b) <= n:
        return text
    return b[:n].decode("utf-8", errors="replace") + f"\n[... truncated, {len(b)} bytes total]"


def _run_cmd(cmd: str, cwd: str, timeout: int) -> tuple[int, str, str]:
    """Run cmd via /bin/bash -lc, return (exit_code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", cmd],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + f"\n[timeout after {timeout}s]"


class Verify(
    BaseTool[VerifyArgs, VerifyResult, VerifyConfig, BaseToolState],
    ToolUIData[VerifyArgs, VerifyResult],
):
    description: ClassVar[str] = (
        "Verify a success criterion programmatically before claiming "
        "done. Runs a shell command (or checks a file) and matches the "
        "result against `expect`. Modes: contains (default), "
        "not_contains, regex, equals, exit_code, file_exists, "
        "file_contains. Use AFTER making a change to confirm it worked. "
        "Examples: verify(criterion='tests pass', command='pytest -q', "
        "expect='passed', expect_mode='contains'); "
        "verify(criterion='no flake8 warnings', command='flake8', "
        "expect_mode='exit_code', expect='0'); "
        "verify(criterion='README has section', expect_mode='file_contains', "
        "expect='README.md::## Math Tool')."
    )

    @classmethod
    def format_call_display(cls, args: VerifyArgs) -> ToolCallDisplay:
        c = args.criterion[:60] + ("..." if len(args.criterion) > 60 else "")
        return ToolCallDisplay(summary=f"verify [{args.expect_mode}]: {c}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, VerifyResult):
            r = event.result
            if not r.ok:
                return ToolResultDisplay(success=False, message=f"verify error: {r.error[:80]}")
            mark = "PASS" if r.passed else "FAIL"
            return ToolResultDisplay(
                success=r.passed, message=f"{mark}: {r.reason[:80]}",
            )
        return ToolResultDisplay(success=True, message="verify complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Verifying"

    def resolve_permission(self, args: VerifyArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: VerifyArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | VerifyResult, None]:
        # ── File-only modes (no shell) ─────────────────────────────────
        if args.expect_mode == "file_exists":
            if not args.expect:
                yield VerifyResult(
                    ok=False, criterion=args.criterion,
                    expect_mode=args.expect_mode, passed=False,
                    error="expect required (path)",
                )
                return
            p = Path(args.expect).expanduser()
            passed = p.exists()
            yield VerifyResult(
                ok=True, criterion=args.criterion, expect_mode="file_exists",
                passed=passed,
                reason=f"{'exists' if passed else 'missing'}: {p}",
            )
            return

        if args.expect_mode == "file_contains":
            if "::" not in args.expect:
                yield VerifyResult(
                    ok=False, criterion=args.criterion,
                    expect_mode=args.expect_mode, passed=False,
                    error="file_contains expects 'PATH::SUBSTRING'",
                )
                return
            path_str, _, needle = args.expect.partition("::")
            p = Path(path_str.strip()).expanduser()
            if not p.is_file():
                yield VerifyResult(
                    ok=True, criterion=args.criterion,
                    expect_mode="file_contains", passed=False,
                    reason=f"file missing: {p}",
                )
                return
            try:
                text = p.read_text(errors="replace")
            except OSError as e:
                yield VerifyResult(
                    ok=False, criterion=args.criterion,
                    expect_mode="file_contains", passed=False,
                    error=f"read failed: {e}",
                )
                return
            passed = needle in text
            yield VerifyResult(
                ok=True, criterion=args.criterion, expect_mode="file_contains",
                passed=passed,
                reason=(f"found {needle!r} in {p}" if passed
                        else f"{needle!r} NOT in {p}"),
            )
            return

        # ── Shell-based modes ──────────────────────────────────────────
        if not args.command:
            yield VerifyResult(
                ok=False, criterion=args.criterion,
                expect_mode=args.expect_mode, passed=False,
                error=f"command required for mode {args.expect_mode}",
            )
            return

        cwd = args.cwd or os.getcwd()
        rc, stdout, stderr = _run_cmd(args.command, cwd, args.timeout)
        combined = (stdout or "") + (("\n" + stderr) if stderr else "")
        truncated = _truncate(combined)

        if args.expect_mode == "exit_code":
            try:
                want = int(args.expect)
            except (ValueError, TypeError):
                yield VerifyResult(
                    ok=False, criterion=args.criterion,
                    expect_mode="exit_code", passed=False, exit_code=rc,
                    output=truncated,
                    error=f"exit_code mode requires integer expect, got {args.expect!r}",
                )
                return
            passed = rc == want
            yield VerifyResult(
                ok=True, criterion=args.criterion, expect_mode="exit_code",
                passed=passed, exit_code=rc, output=truncated,
                reason=f"exit_code={rc} (wanted {want})",
            )
            return

        if not args.expect:
            yield VerifyResult(
                ok=False, criterion=args.criterion,
                expect_mode=args.expect_mode, passed=False, exit_code=rc,
                output=truncated, error=f"expect required for mode {args.expect_mode}",
            )
            return

        if args.expect_mode == "contains":
            passed = args.expect in combined
            reason = f"{'found' if passed else 'NOT found'}: {args.expect[:60]}"
        elif args.expect_mode == "not_contains":
            passed = args.expect not in combined
            reason = f"{'absent' if passed else 'unexpected presence'}: {args.expect[:60]}"
        elif args.expect_mode == "regex":
            try:
                m = re.search(args.expect, combined)
            except re.error as e:
                yield VerifyResult(
                    ok=False, criterion=args.criterion, expect_mode="regex",
                    passed=False, exit_code=rc, output=truncated,
                    error=f"invalid regex: {e}",
                )
                return
            passed = m is not None
            reason = (f"regex matched at offset {m.start()}" if passed
                      else f"regex no match: {args.expect[:60]}")
        elif args.expect_mode == "equals":
            passed = combined.strip() == args.expect.strip()
            reason = "equal" if passed else "not equal"
        else:
            yield VerifyResult(
                ok=False, criterion=args.criterion,
                expect_mode=args.expect_mode, passed=False, exit_code=rc,
                output=truncated,
                error=f"unknown expect_mode: {args.expect_mode}",
            )
            return

        yield VerifyResult(
            ok=True, criterion=args.criterion, expect_mode=args.expect_mode,
            passed=passed, exit_code=rc, output=truncated, reason=reason,
        )
