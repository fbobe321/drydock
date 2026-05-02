from __future__ import annotations

from collections.abc import AsyncGenerator
import difflib
from pathlib import Path
import re
import shutil
from typing import ClassVar, NamedTuple, final

import anyio
from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.tools.utils import resolve_file_tool_permission
from drydock.core.types import ToolResultEvent, ToolStreamEvent

SEARCH_REPLACE_BLOCK_RE = re.compile(
    r"<{5,}\s*(?:SEARCH\s*)+\r?\n(.*?)\r?\n?={5,}\r?\n(.*?)\r?\n?>{5,}\s*(?:REPLACE\s*)+",
    flags=re.DOTALL,
)

SEARCH_REPLACE_BLOCK_WITH_FENCE_RE = re.compile(
    r"```[\s\S]*?\n<{5,}\s*(?:SEARCH\s*)+\r?\n(.*?)\r?\n?={5,}\r?\n(.*?)\r?\n?>{5,}\s*(?:REPLACE\s*)+\s*\n```",
    flags=re.DOTALL,
)


class SearchReplaceBlock(NamedTuple):
    search: str
    replace: str


class FuzzyMatch(NamedTuple):
    similarity: float
    start_line: int
    end_line: int
    text: str


class BlockApplyResult(NamedTuple):
    content: str
    applied: int
    errors: list[str]
    warnings: list[str]


class SearchReplaceArgs(BaseModel):
    file_path: str = Field(default="", description="Path to the file to edit")
    content: str = Field(default="", description="SEARCH/REPLACE blocks or JSON with old_string/new_string")
    # Gemma 4 sometimes sends these directly instead of in content
    old_string: str | None = Field(default=None, description="Text to find (alternative to content blocks)")
    new_string: str | None = Field(default=None, description="Replacement text (alternative to content blocks)")


class SearchReplaceResult(BaseModel):
    file: str
    blocks_applied: int
    lines_changed: int
    content: str
    warnings: list[str] = Field(default_factory=list)


class SearchReplaceConfig(BaseToolConfig):
    max_content_size: int = 100_000
    create_backup: bool = False
    fuzzy_threshold: float = 0.9


class SearchReplace(
    BaseTool[
        SearchReplaceArgs, SearchReplaceResult, SearchReplaceConfig, BaseToolState
    ],
    ToolUIData[SearchReplaceArgs, SearchReplaceResult],
):
    description: ClassVar[str] = (
        "Replace sections of files using SEARCH/REPLACE blocks. "
        "Supports fuzzy matching and detailed error reporting. "
        "Format: <<<<<<< SEARCH\\n[text]\\n=======\\n[replacement]\\n>>>>>>> REPLACE"
    )

    @classmethod
    def format_call_display(cls, args: SearchReplaceArgs) -> ToolCallDisplay:
        blocks = cls._parse_search_replace_blocks(args.content)
        return ToolCallDisplay(
            summary=f"Patching {args.file_path} ({len(blocks)} blocks)",
            content=args.content,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, SearchReplaceResult):
            if event.result.blocks_applied == 0:
                return ToolResultDisplay(
                    success=False,
                    message=event.result.content[:120].split("\n")[0],
                    warnings=event.result.warnings,
                )
            return ToolResultDisplay(
                success=True,
                message=f"Applied {event.result.blocks_applied} block{'' if event.result.blocks_applied == 1 else 's'}",
                warnings=event.result.warnings,
            )

        return ToolResultDisplay(success=True, message="Patch applied")

    @classmethod
    def get_status_text(cls) -> str:
        return "Editing files"

    def resolve_permission(self, args: SearchReplaceArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.file_path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @final
    async def run(
        self, args: SearchReplaceArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | SearchReplaceResult, None]:
        try:
            file_path, search_replace_blocks = self._prepare_and_validate_args(args)
        except ToolError as e:
            err_msg = str(e)
            if err_msg.startswith("Empty content provided.") or err_msg.startswith("File path is required."):
                # Model sent search_replace with missing content or file_path.
                # Convert to a soft result so the model corrects — a ToolError
                # causes panic-retry loops (feedback: never raise ToolError for
                # loop detection). Track count to escalate on repeat offenses.
                empty_state = self.state.__dict__.setdefault("_sr_empty_history", {})
                key = args.file_path.strip() or "<no-path>"
                entry = empty_state.get(key, {"count": 0})
                entry["count"] += 1
                empty_state[key] = entry
                count = entry["count"]
                escalate = count >= 2
                extra = ""
                if escalate:
                    # Show project files so model can recover context
                    try:
                        py_files = sorted(
                            str(p.relative_to(Path.cwd()))
                            for p in Path.cwd().rglob("*.py")
                            if "__pycache__" not in str(p) and ".git" not in str(p)
                        )[:20]
                        extra = (
                            f"\n[This is the #{count} empty search_replace on '{key}'. "
                            f"Stop retrying. Current .py files in project:\n"
                            + "\n".join(f"  {f}" for f in py_files)
                            + "\nUse write_file to create a file, or read_file to "
                            f"see file contents before editing.]"
                        )
                    except Exception:
                        extra = (
                            f"\n[This is the #{count} empty search_replace on '{key}'. "
                            "Stop retrying. Use write_file or read the file first.]"
                        )
                yield SearchReplaceResult(
                    file=key,
                    blocks_applied=0,
                    lines_changed=0,
                    content=err_msg + extra,
                )
                return
            if err_msg.startswith("Path is not a file:"):
                # Model passed a directory instead of a file path.
                # Return a result (not raise) so the model corrects the call
                # rather than retrying the same directory path in a loop.
                # Track consecutive calls to escalate guidance on 2nd+ offense.
                dir_path_str = args.file_path.strip()
                dir_path = Path(dir_path_str).expanduser()
                if not dir_path.is_absolute():
                    dir_path = Path.cwd() / dir_path
                dir_path = dir_path.resolve()
                if dir_path.is_dir():
                    dir_state = self.state.__dict__.setdefault("_sr_dir_history", {})
                    dir_key = str(dir_path)
                    dir_entry = dir_state.get(dir_key, {"count": 0})
                    dir_entry["count"] += 1
                    dir_state[dir_key] = dir_entry
                    dir_count = dir_entry["count"]
                    try:
                        files = sorted(p.name for p in dir_path.iterdir() if p.is_file())
                    except OSError:
                        files = []
                    files_list = "\n".join(f"  {f}" for f in files[:20]) or "  (empty directory)"
                    if dir_count >= 2:
                        # Escalate: model retried the same directory path — add project listing
                        try:
                            py_files = sorted(
                                str(p.relative_to(Path.cwd()))
                                for p in Path.cwd().rglob("*.py")
                                if "__pycache__" not in str(p) and ".git" not in str(p)
                            )[:20]
                            project_listing = (
                                f"\n[REPEATED ERROR #{dir_count}: you have called search_replace on "
                                f"this directory {dir_count} times. You MUST specify a filename. "
                                f"Project .py files:\n"
                                + "\n".join(f"  {f}" for f in py_files)
                                + "\nUse the exact path from this list.]"
                            )
                        except Exception:
                            project_listing = (
                                f"\n[REPEATED ERROR #{dir_count}: stop passing the directory. "
                                "Use a full file path like 'tool_agent/cli.py'.]"
                            )
                    else:
                        project_listing = ""
                    yield SearchReplaceResult(
                        file=str(dir_path),
                        blocks_applied=0,
                        lines_changed=0,
                        content=(
                            f"PATH ERROR: '{dir_path}' is a directory, not a file. "
                            f"search_replace requires a path to a specific file.\n"
                            f"Files in that directory:\n{files_list}\n"
                            f"Specify the full path including the filename, e.g. "
                            f"'{dir_path}/{files[0] if files else '<filename.py>'}'"
                            + project_listing
                        ),
                    )
                    return
            if err_msg.startswith("File does not exist:"):
                # Convert to advisory result with directory listing so the
                # model can correct its path without retrying blindly.
                # Previously raised as ToolError → model retried 18+ times
                # (admiral_history shows "retry_after_error:search_replace:
                # <tool_error>search_replace failed: File does not exist").
                bad_path_str = args.file_path.strip()
                bad_path = Path(bad_path_str).expanduser()
                if not bad_path.is_absolute():
                    bad_path = Path.cwd() / bad_path
                bad_path = bad_path.resolve()
                parent = bad_path.parent
                # Track repeat offenses so escalation fires on 2nd+ call.
                fne_state = self.state.__dict__.setdefault("_sr_fne_history", {})
                fne_key = str(bad_path)
                fne_entry = fne_state.get(fne_key, {"count": 0})
                fne_entry["count"] += 1
                fne_state[fne_key] = fne_entry
                fne_count = fne_entry["count"]
                try:
                    if parent.is_dir():
                        sibling_files = sorted(
                            p.name for p in parent.iterdir() if p.is_file()
                        )[:20]
                        dir_listing = (
                            f"Files in {parent}:\n"
                            + "\n".join(f"  {f}" for f in sibling_files)
                        ) if sibling_files else f"Directory {parent} is empty."
                    else:
                        dir_listing = f"Parent directory {parent} does not exist."
                except OSError:
                    dir_listing = f"Could not list {parent}."
                extra = ""
                if fne_count >= 2:
                    try:
                        py_files = sorted(
                            str(p.relative_to(Path.cwd()))
                            for p in Path.cwd().rglob("*.py")
                            if "__pycache__" not in str(p) and ".git" not in str(p)
                        )[:20]
                        extra = (
                            f"\n[REPEATED ERROR #{fne_count}: '{bad_path.name}' still "
                            f"does not exist. Stop retrying this path. "
                            f"Project .py files you can edit:\n"
                            + "\n".join(f"  {f}" for f in py_files)
                            + "\nTo CREATE a new file use write_file instead of search_replace.]"
                        )
                    except Exception:
                        extra = (
                            f"\n[REPEATED ERROR #{fne_count}: use write_file to create "
                            f"'{bad_path.name}' before trying to edit it.]"
                        )
                yield SearchReplaceResult(
                    file=bad_path_str,
                    blocks_applied=0,
                    lines_changed=0,
                    content=(
                        f"FILE NOT FOUND: '{bad_path}' does not exist and cannot be edited.\n"
                        f"{dir_listing}\n"
                        f"If you need to CREATE this file, use write_file. "
                        f"If you meant to edit a different file, use the exact path from the listing above."
                        + extra
                    ),
                )
                return
            if err_msg.startswith("NO_BLOCKS:"):
                # Gemma 4 sent raw code without SEARCH/REPLACE markers.
                # CAREFULLY fall back to full file overwrite — but refuse if
                # the raw content is much shorter than the existing file
                # (would be catastrophic data loss). See CLAUDE.md learning
                # #39: drydock nuked a 5171-char cli.py with a 16-line fragment.
                raw_content = err_msg[len("NO_BLOCKS:"):]
                # Guard: if the raw content contains SEARCH/REPLACE markers, it's
                # a malformed block (e.g. missing >>>>>>> REPLACE closer). Writing
                # it verbatim would corrupt the file with conflict markers, causing
                # a retry loop. Return an error instead.
                if "<<<<<<<" in raw_content or ">>>>>>>" in raw_content:
                    yield SearchReplaceResult(
                        file=args.file_path.strip() or "(unknown)",
                        blocks_applied=0,
                        lines_changed=0,
                        warnings=[],
                        content=(
                            "PARSE ERROR: your search_replace content looks like a "
                            "SEARCH/REPLACE block but it could not be parsed — likely "
                            "the >>>>>>> REPLACE closer is missing or the markers are "
                            "malformed. Fix the format:\n"
                            "<<<<<<< SEARCH\n"
                            "[exact text to find]\n"
                            "=======\n"
                            "[replacement text]\n"
                            ">>>>>>> REPLACE\n"
                            "Do NOT send conflict markers as literal file content."
                        ),
                    )
                    return
                file_path_str = args.file_path.strip()
                if file_path_str and len(raw_content) > 20:
                    target = Path(file_path_str).expanduser()
                    if not target.is_absolute():
                        target = Path.cwd() / target
                    target = target.resolve()
                    if target.exists():
                        import logging as _log
                        existing_size = target.stat().st_size
                        # SAFETY CHECK: a raw-code overwrite that would shrink
                        # the file by >50% is almost certainly a partial patch
                        # the model intended to APPEND, not a full rewrite.
                        # Old behavior REFUSED — model retried the same broken
                        # call in a loop. Try APPEND first when the combined
                        # file still parses cleanly (Python only); fall back
                        # to the directive REFUSED only if append would break
                        # the syntax.
                        if existing_size > 500 and len(raw_content) < existing_size * 0.5:
                            _log.getLogger(__name__).warning(
                                "search_replace: shrink-overwrite would lose %d%% "
                                "of %s (existing=%d, new=%d) — trying APPEND instead",
                                100 - int(len(raw_content) * 100 / existing_size),
                                target, existing_size, len(raw_content),
                            )
                            existing_text = target.read_text(errors="replace")
                            sep = "\n\n" if not existing_text.endswith("\n") else "\n"
                            combined = existing_text + sep + raw_content + (
                                "" if raw_content.endswith("\n") else "\n"
                            )
                            append_safe = True
                            if target.suffix == ".py":
                                try:
                                    import ast as _ast
                                    _ast.parse(combined)
                                except SyntaxError:
                                    append_safe = False
                            if append_safe:
                                _log.getLogger(__name__).info(
                                    "search_replace: APPEND %d chars to %s (parsed clean)",
                                    len(raw_content), target,
                                )
                                await self._write_file(target, combined)
                                self.state.__dict__.setdefault(
                                    "_sr_refused_raw_history", {}
                                ).pop(str(target), None)
                                yield SearchReplaceResult(
                                    file=str(target),
                                    blocks_applied=1,
                                    lines_changed=raw_content.count("\n") + 1,
                                    warnings=[
                                        "Appended raw content to end of file (no "
                                        "SEARCH/REPLACE blocks sent). Use proper "
                                        "SEARCH/REPLACE blocks for non-append edits."
                                    ],
                                    content=raw_content[:100] + "...",
                                )
                                return
                            # Track consecutive REFUSED-raw failures per file
                            # so we can escalate when the model retries the same
                            # broken raw-content call. Pattern observed in
                            # admiral logs: 14 retry_after_error:search_replace
                            # fires per 6h, all with "REFUSED: the raw content".
                            refused_state = self.state.__dict__.setdefault(
                                "_sr_refused_raw_history", {}
                            )
                            refused_key = str(target)
                            refused_entry = refused_state.get(
                                refused_key, {"count": 0, "last_len": 0}
                            )
                            refused_entry["count"] += 1
                            refused_entry["last_len"] = len(raw_content)
                            refused_state[refused_key] = refused_entry
                            base_msg = (
                                f"REFUSED: the raw content you sent ({len(raw_content)} "
                                f"chars) is much shorter than the existing {target.name} "
                                f"({existing_size} chars), and appending it would break "
                                f"the file's syntax."
                            )
                            if refused_entry["count"] >= 2:
                                head = existing_text[:1500] if "existing_text" in locals() else target.read_text(errors="replace")[:1500]
                                tail_src = existing_text if "existing_text" in locals() else target.read_text(errors="replace")
                                tail = tail_src[-800:] if len(tail_src) > 2300 else ""
                                tail_block = (
                                    f"\n-----FILE TAIL (last 800 chars)-----\n{tail}\n"
                                    if tail else ""
                                )
                                yield SearchReplaceResult(
                                    file=str(target),
                                    blocks_applied=0,
                                    lines_changed=0,
                                    content=(
                                        f"{base_msg}\n\n[LOOP-BREAKER: this is the "
                                        f"#{refused_entry['count']} consecutive REFUSED on "
                                        f"{target.name}. Stop sending the same raw content. "
                                        f"Actual file content (first 1500 chars of "
                                        f"{existing_size} bytes):\n"
                                        f"-----FILE HEAD-----\n{head}\n-----FILE END HEAD-----"
                                        f"{tail_block}\n"
                                        f"Choose ONE: (a) call write_file with overwrite=True "
                                        f"to replace the whole file with your raw content, OR "
                                        f"(b) send a proper SEARCH/REPLACE block anchored on "
                                        f"text from the file head/tail above.]"
                                    ),
                                )
                                return
                            yield SearchReplaceResult(
                                file=str(target),
                                blocks_applied=0,
                                lines_changed=0,
                                content=(
                                    f"{base_msg} To add new code, use a SEARCH/REPLACE "
                                    f"block anchored on the last line of the file:\n"
                                    f"<<<<<<< SEARCH\n"
                                    f"[the file's actual final line, copied exactly]\n"
                                    f"=======\n"
                                    f"[the same final line]\n\n"
                                    f"[your new code]\n"
                                    f">>>>>>> REPLACE\n"
                                    f"If you intended a full rewrite, use write_file instead."
                                ),
                            )
                            return
                        _log.getLogger(__name__).info(
                            "search_replace: no blocks — overwriting %s", target,
                        )
                        await self._write_file(target, raw_content)
                        self.state.__dict__.setdefault(
                            "_sr_refused_raw_history", {}
                        ).pop(str(target), None)
                        yield SearchReplaceResult(
                            file=str(target),
                            blocks_applied=1,
                            lines_changed=0,
                            warnings=["Wrote entire file (no SEARCH/REPLACE blocks). "
                                      "Use write_file next time."],
                            content=raw_content[:100] + "...",
                        )
                        return
            raise

        # Read-before-Edit enforcement (Claude Code tool contract).
        # Editing a file without having seen it is the #1 cause of the
        # "SEARCH text not found" failure cascade. Require the model to
        # have read this path (or just written it) this session.
        # Structured rejection (not ToolError) — model sees guidance in
        # the result content and pivots to read_file.
        read_state = ctx.read_file_state if ctx else None
        path_key = str(file_path)
        if read_state is not None and file_path.exists():
            prior = read_state.get(path_key)
            if prior is None:
                yield SearchReplaceResult(
                    file=path_key,
                    blocks_applied=0,
                    lines_changed=0,
                    warnings=[],
                    content=(
                        "<system-reminder>\n"
                        f"{file_path.name} has not been read this session. "
                        "Use read_file first so you can see the current "
                        "contents — then search_replace with the actual "
                        "text from the file. This edit was NOT applied.\n"
                        "</system-reminder>"
                    ),
                )
                return
            try:
                current_mtime = file_path.stat().st_mtime_ns
            except OSError:
                current_mtime = 0
            if prior.get("timestamp") and current_mtime and current_mtime > prior["timestamp"]:
                yield SearchReplaceResult(
                    file=path_key,
                    blocks_applied=0,
                    lines_changed=0,
                    warnings=[],
                    content=(
                        "<system-reminder>\n"
                        f"{file_path.name} was modified on disk since your "
                        "last read. Re-read before editing to avoid "
                        "clobbering changes you haven't seen. This edit "
                        "was NOT applied.\n"
                        "</system-reminder>"
                    ),
                )
                return

        # Injection guard: scan replacement content for suspicious patterns
        from drydock.core.tools.injection_guard import check_content_for_injection
        if warning := check_content_for_injection(args.content, args.file_path):
            import logging
            logging.getLogger(__name__).warning("search_replace: %s", warning)

        original_content = await self._read_file(file_path)

        # Detect placeholder/omission patterns that would delete code
        PLACEHOLDER_PATTERNS = [
            "# rest of code", "# ... rest", "# unchanged",
            "// rest of code", "// ... rest", "// unchanged",
            "# TODO: implement", "pass  # placeholder",
            "...",  # bare ellipsis as entire replacement
        ]
        for block in search_replace_blocks:
            replacement = block.replace.strip()
            if replacement in PLACEHOLDER_PATTERNS or any(
                p in replacement.lower() for p in [
                    "rest of code", "rest of the code", "unchanged",
                    "remaining code", "code continues", "etc.",
                ]
            ):
                raise ToolError(
                    f"Your replacement contains a placeholder ('{replacement[:50]}') "
                    f"that would delete existing code. Provide the COMPLETE replacement "
                    f"code, not a summary. If the code is unchanged, don't edit it."
                )

        # Detect no-op edits where SEARCH and REPLACE are byte-identical.
        # Short-circuit before _apply_blocks so we never reach the ambiguous
        # "edited successfully (+0 lines)" path for this structural no-op.
        noop_blocks = [b for b in search_replace_blocks if b.search == b.replace]
        if noop_blocks:
            yield SearchReplaceResult(
                file=str(file_path),
                blocks_applied=0,
                lines_changed=0,
                warnings=[],
                content=(
                    f"{file_path.name}: ALREADY CORRECT — the SEARCH and REPLACE text "
                    f"are byte-identical, so this block can never make any change. "
                    f"Re-read the file with read_file, identify what you actually need "
                    f"to change, and send a corrected SEARCH/REPLACE block."
                ),
            )
            return

        block_result = self._apply_blocks(
            original_content,
            search_replace_blocks,
            file_path,
            self.config.fuzzy_threshold,
        )

        if block_result.errors:
            error_message = "SEARCH/REPLACE blocks failed:\n" + "\n\n".join(
                block_result.errors
            )
            if block_result.warnings:
                error_message += "\n\nWarnings encountered:\n" + "\n".join(
                    block_result.warnings
                )

            # Mechanical loop-breaker: if search_replace has failed with
            # "Search text not found" on the SAME file 2+ times in a row,
            # embed the current file head in the error so the model sees
            # actual file state and can't keep retrying phantom edits.
            # (One session had 8 consecutive identical search_replace
            # failures with 0 behavior change. Nudges didn't help.)
            state = self.state.__dict__.setdefault("_sr_fail_history", {})
            fail_key = str(file_path)
            entry = state.get(fail_key, {"count": 0})
            entry["count"] += 1
            state[fail_key] = entry
            if entry["count"] == 1:
                # First failure: embed file head so model sees actual content
                # without waiting for a second retry cycle. Reduces
                # retry_after_error:search_replace events significantly.
                try:
                    head = original_content[:1500]
                    line_count = original_content.count("\n")
                    error_message += (
                        f"\n\n[HINT: search text not found in {file_path.name}. "
                        f"File may have changed. Current file head "
                        f"({line_count} lines total):\n"
                        f"-----FILE HEAD-----\n{head}\n-----FILE HEAD END-----\n"
                        f"Adjust your SEARCH text to match the actual content above.]"
                    )
                except Exception:
                    pass
            elif entry["count"] >= 2:
                try:
                    line_count = original_content.count("\n")
                    count = entry["count"]
                    if count >= 3:
                        # Show full file (up to 4000 chars) and prohibit retry.
                        body = original_content[:4000]
                        tail = (
                            f"\n...[truncated, {line_count} lines total]"
                            if len(original_content) > 4000 else ""
                        )
                        error_message += (
                            f"\n\n[HARD-STOP: this is the #{count} consecutive "
                            f"search_replace failure on {file_path.name}. "
                            f"Your search text was NOT found in the file. "
                            f"DO NOT retry search_replace with the same or similar text. "
                            f"REQUIRED action: call write_file with overwrite=True "
                            f"and provide the complete new file content. "
                            f"Full file content below — use this as the basis "
                            f"for your write_file call:\n"
                            f"-----FILE START-----\n{body}{tail}\n-----FILE END-----]"
                        )
                    else:
                        head = original_content[:2000]
                        error_message += (
                            f"\n\n[LOOP-BREAKER: this is the #{count} "
                            f"consecutive search_replace failure on "
                            f"{file_path.name}. Stop retrying the same search. "
                            f"Actual file content (first 2000 chars of "
                            f"{line_count} lines):\n"
                            f"-----FILE START-----\n{head}\n-----FILE END-----\n"
                            f"Use THIS exact text as your search target, OR "
                            f"abandon this edit and try write_file.]"
                        )
                except Exception:
                    pass

            # Return advisory result instead of raising ToolError.
            # Hard ToolError blocks cause their own retry loops on longer tasks
            # (model panics and re-calls the same failing search_replace). The
            # advisory result delivers the same guidance message + file head
            # without triggering the panic-retry pattern.
            yield SearchReplaceResult(
                file=str(file_path),
                blocks_applied=0,
                lines_changed=0,
                content=error_message,
            )
            return
        # Success → reset the fail counter for this file
        state = self.state.__dict__.setdefault("_sr_fail_history", {})
        state.pop(str(file_path), None)
        refused_state = self.state.__dict__.setdefault(
            "_sr_refused_raw_history", {}
        )
        refused_state.pop(str(file_path), None)

        modified_content = block_result.content

        # Calculate line changes
        if modified_content == original_content:
            lines_changed = 0
            # The SEARCH text was found and the REPLACE was identical — nothing
            # actually changed. Tell the model clearly so it doesn't retry.
            yield SearchReplaceResult(
                file=str(file_path),
                blocks_applied=block_result.applied,
                lines_changed=0,
                warnings=block_result.warnings,
                content=(
                    f"{file_path.name}: ALREADY CORRECT — the search text was found "
                    f"but the replacement is identical to the current content. "
                    f"No change was written. The file already has the desired state. "
                    f"Move on to the next task."
                ),
            )
            return
        else:
            original_lines = len(original_content.splitlines())
            new_lines = len(modified_content.splitlines())
            lines_changed = new_lines - original_lines

            try:
                if self.config.create_backup:
                    await self._backup_file(file_path)
            except Exception:
                pass

            await self._write_file(file_path, modified_content)

            # Update read_file_state so chained edits don't trip Read-
            # before-Edit. We just wrote — we know disk state.
            if read_state is not None:
                try:
                    new_mtime = file_path.stat().st_mtime_ns
                except OSError:
                    new_mtime = 0
                read_state[path_key] = {
                    "content": modified_content,
                    "timestamp": new_mtime,
                    "offset": 0,
                    "limit": None,
                }

            # Auto-verify syntax for Python files
            if file_path.suffix == ".py":
                try:
                    import ast
                    ast.parse(modified_content)
                except SyntaxError as e:
                    block_result.warnings.append(
                        f"⚠ SYNTAX ERROR after edit at line {e.lineno}: {e.msg}. "
                        f"Fix this before continuing."
                    )

        # Terse success content (Claude Code pattern) — don't echo the
        # SEARCH/REPLACE payload back. Model already has it in history;
        # echoing wastes context and tempts re-reading. Warnings still go
        # through so the model sees actionable issues.
        yield SearchReplaceResult(
            file=str(file_path),
            blocks_applied=block_result.applied,
            lines_changed=lines_changed,
            warnings=block_result.warnings,
            content=(
                f"{file_path.name} edited successfully "
                f"({block_result.applied} block(s), "
                f"{lines_changed:+d} line(s))."
            ),
        )

    @final
    def _prepare_and_validate_args(
        self, args: SearchReplaceArgs
    ) -> tuple[Path, list[SearchReplaceBlock]]:
        file_path_str = args.file_path.strip()
        content = args.content.strip()

        # Handle direct old_string/new_string args (Gemma 4 sometimes sends these)
        if not content and args.old_string is not None and args.new_string is not None:
            content = f"<<<<<<< SEARCH\n{args.old_string}\n=======\n{args.new_string}\n>>>>>>> REPLACE"

        # Try to extract file_path from content if missing
        if not file_path_str and content:
            # Look for file path in the content (common when model puts everything in content)
            path_match = re.search(r'(?:file[_\s]?path|path|file)[\s:="\']+([^\s"\']+\.py)', content[:200], re.IGNORECASE)
            if path_match:
                file_path_str = path_match.group(1)

        # Last resort: if file_path is still missing but we have SEARCH text,
        # scan project files for a match.  Gemma 4 frequently drops file_path.
        # Limit scan to avoid hanging on large repos.
        if not file_path_str and content:
            blocks = self._parse_search_replace_blocks(content)
            if blocks:
                search_text = blocks[0].search.strip()
                if search_text and len(search_text) >= 10:
                    project_root = Path.cwd()
                    candidates: list[Path] = []
                    files_checked = 0
                    for ext in ("*.py",):  # Only scan Python files
                        for f in project_root.rglob(ext):
                            if files_checked > 200:
                                break
                            if "__pycache__" in str(f) or ".git" in str(f):
                                continue
                            files_checked += 1
                            try:
                                text = f.read_text(encoding="utf-8", errors="replace")
                                if len(text) > 100_000:
                                    continue
                                if search_text in text:
                                    candidates.append(f)
                            except Exception:
                                continue
                    if len(candidates) == 1:
                        file_path_str = str(candidates[0])
                    elif candidates:
                        candidates.sort(key=lambda p: len(str(p)))
                        file_path_str = str(candidates[0])

        if not file_path_str:
            raise ToolError(
                "File path is required. Use: search_replace(file_path='path/to/file.py', content='...')"
            )

        if len(content) > self.config.max_content_size:
            raise ToolError(
                f"Content size ({len(content)} bytes) exceeds max_content_size "
                f"({self.config.max_content_size} bytes)"
            )

        if not content:
            raise ToolError(
                "Empty content provided. You must include SEARCH/REPLACE blocks.\n"
                "Format:\n"
                "<<<<<<< SEARCH\n"
                "exact text to find\n"
                "=======\n"
                "replacement text\n"
                ">>>>>>> REPLACE\n"
                "If you want to create a new file instead, use write_file."
            )

        project_root = Path.cwd()
        file_path = Path(file_path_str).expanduser()
        if not file_path.is_absolute():
            file_path = project_root / file_path
        file_path = file_path.resolve()

        if not file_path.exists():
            raise ToolError(f"File does not exist: {file_path}")

        if not file_path.is_file():
            raise ToolError(f"Path is not a file: {file_path}")

        search_replace_blocks = self._parse_search_replace_blocks(content)
        if not search_replace_blocks:
            raise ToolError(
                "NO_BLOCKS:" + content  # sentinel for run() to handle fallback
            )

        # Detect garbled token output — only flag obvious corruption
        # (mixed angle brackets + repeated letters like <<t<ttt<t), not normal repeats
        for block in search_replace_blocks:
            if re.search(r'<[a-z]<[a-z]{2,}<[a-z]', block.search):
                raise ToolError(
                    "Your search text appears garbled (token corruption). "
                    "Use write_file to rewrite the function instead of search_replace."
                )

        return file_path, search_replace_blocks

    async def _read_file(self, file_path: Path) -> str:
        import asyncio

        def _sync_read():
            with open(file_path, encoding="utf-8") as f:
                return f.read()

        try:
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, _sync_read), timeout=10,
            )
        except asyncio.TimeoutError:
            raise ToolError(f"Timed out reading {file_path} after 10s.")
        except UnicodeDecodeError as e:
            raise ToolError(f"Unicode decode error reading {file_path}: {e}") from e
        except PermissionError:
            raise ToolError(f"Permission denied reading file: {file_path}")
        except Exception as e:
            raise ToolError(f"Unexpected error reading {file_path}: {e}") from e

    async def _backup_file(self, file_path: Path) -> None:
        shutil.copy2(file_path, file_path.with_suffix(file_path.suffix + ".bak"))

    async def _write_file(self, file_path: Path, content: str) -> None:
        import asyncio

        def _sync_write():
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, _sync_write), timeout=10,
            )
        except asyncio.TimeoutError:
            raise ToolError(f"Timed out writing {file_path} after 10s.")
        except PermissionError:
            raise ToolError(f"Permission denied writing to file: {file_path}")
        except OSError as e:
            raise ToolError(f"OS error writing to {file_path}: {e}") from e
        except Exception as e:
            raise ToolError(f"Unexpected error writing to {file_path}: {e}") from e

    @final
    @staticmethod
    def _apply_blocks(
        content: str,
        blocks: list[SearchReplaceBlock],
        filepath: Path,
        fuzzy_threshold: float = 0.9,
    ) -> BlockApplyResult:
        applied = 0
        errors: list[str] = []
        warnings: list[str] = []
        current_content = content

        for i, (search, replace) in enumerate(blocks, 1):
            if search not in current_content:
                # Check if this change was ALREADY APPLIED — the replacement
                # text is in the file but the search text is not.  This is the
                # #1 cause of edit loops: the model successfully edits a file
                # then retries the same edit because it doesn't realise it
                # already worked.
                if (replace and replace.strip()
                        and len(replace.strip()) >= 10
                        and replace.strip() in current_content):
                    warnings.append(
                        f"Block {i}: ALREADY APPLIED — the replacement text already "
                        f"exists in {filepath.name}. The search text is gone because "
                        f"a previous edit already made this change. "
                        f"Move on to the next task."
                    )
                    # Count as "applied" so we don't error — the file is correct
                    applied += 1
                    continue
                # Also detect deletion that already happened (search text removed,
                # replace is empty — the line is simply gone).  Only trigger
                # for meaningful search strings to avoid false positives.
                if (not replace or not replace.strip()) and len(search.strip()) >= 10:
                    warnings.append(
                        f"Block {i}: ALREADY APPLIED — the text you want to remove "
                        f"is already absent from {filepath.name}. "
                        f"Move on to the next task."
                    )
                    applied += 1
                    continue

                # Try auto-applying high-confidence fuzzy match (>= 0.95 similarity)
                auto_apply_threshold = max(fuzzy_threshold, 0.95)
                best_match = SearchReplace._find_best_fuzzy_match(
                    current_content, search, auto_apply_threshold
                )
                if best_match and best_match.similarity >= auto_apply_threshold:
                    # Auto-apply: replace the fuzzy-matched text with the replacement
                    current_content = current_content.replace(best_match.text, replace, 1)
                    applied += 1
                    similarity_pct = best_match.similarity * 100
                    warnings.append(
                        f"Block {i}: auto-applied via fuzzy match ({similarity_pct:.1f}% similarity, "
                        f"lines {best_match.start_line}-{best_match.end_line}). "
                        f"Whitespace differences were normalized."
                    )
                    continue

                context = SearchReplace._find_search_context(current_content, search)
                fuzzy_context = SearchReplace._find_fuzzy_match_context(
                    current_content, search, fuzzy_threshold
                )

                error_msg = (
                    f"SEARCH/REPLACE block {i} failed: Search text not found in {filepath}\n"
                    f"Search text was:\n{search!r}\n"
                    f"Context analysis:\n{context}"
                )

                if fuzzy_context:
                    error_msg += f"\n{fuzzy_context}"

                error_msg += (
                    "\nDebugging tips:\n"
                    "1. Check for exact whitespace/indentation match\n"
                    "2. Verify line endings match the file exactly (\\r\\n vs \\n)\n"
                    "3. Ensure the search text hasn't been modified by previous blocks or user edits\n"
                    "4. Check for typos or case sensitivity issues"
                )

                errors.append(error_msg)
                continue

            occurrences = current_content.count(search)
            if occurrences > 1:
                warning_msg = (
                    f"Search text in block {i} appears {occurrences} times in the file. "
                    f"Only the first occurrence will be replaced. Consider making your "
                    f"search pattern more specific to avoid unintended changes."
                )
                warnings.append(warning_msg)

            current_content = current_content.replace(search, replace, 1)
            applied += 1

        return BlockApplyResult(
            content=current_content, applied=applied, errors=errors, warnings=warnings
        )

    @final
    @staticmethod
    def _find_fuzzy_match_context(
        content: str, search_text: str, threshold: float = 0.9
    ) -> str | None:
        best_match = SearchReplace._find_best_fuzzy_match(
            content, search_text, threshold
        )

        if not best_match:
            return None

        diff = SearchReplace._create_unified_diff(
            search_text, best_match.text, "SEARCH", "CLOSEST MATCH"
        )

        similarity_pct = best_match.similarity * 100

        return (
            f"Closest fuzzy match (similarity {similarity_pct:.1f}%) "
            f"at lines {best_match.start_line}–{best_match.end_line}:\n"
            f"```diff\n{diff}\n```"
        )

    @final
    @staticmethod
    def _find_best_fuzzy_match(  # noqa: PLR0914
        content: str, search_text: str, threshold: float = 0.9
    ) -> FuzzyMatch | None:
        content_lines = content.split("\n")
        search_lines = search_text.split("\n")
        window_size = len(search_lines)

        if window_size == 0:
            return None

        non_empty_search = [line for line in search_lines if line.strip()]
        if not non_empty_search:
            return None

        first_anchor = non_empty_search[0]
        last_anchor = (
            non_empty_search[-1] if len(non_empty_search) > 1 else first_anchor
        )

        candidate_starts = set()
        spread = 5

        for i, line in enumerate(content_lines):
            if first_anchor in line or last_anchor in line:
                start_min = max(0, i - spread)
                start_max = min(len(content_lines) - window_size + 1, i + spread + 1)
                for s in range(start_min, start_max):
                    candidate_starts.add(s)

        if not candidate_starts:
            max_positions = min(len(content_lines) - window_size + 1, 100)
            candidate_starts = set(range(0, max_positions))

        best_match = None
        best_similarity = 0.0

        for start in candidate_starts:
            end = start + window_size
            window_text = "\n".join(content_lines[start:end])

            matcher = difflib.SequenceMatcher(None, search_text, window_text)
            similarity = matcher.ratio()

            if similarity >= threshold and similarity > best_similarity:
                best_similarity = similarity
                best_match = FuzzyMatch(
                    similarity=similarity,
                    start_line=start + 1,  # 1-based line numbers
                    end_line=end,
                    text=window_text,
                )

        return best_match

    @final
    @staticmethod
    def _create_unified_diff(
        text1: str, text2: str, label1: str = "SEARCH", label2: str = "CLOSEST MATCH"
    ) -> str:
        lines1 = text1.splitlines(keepends=True)
        lines2 = text2.splitlines(keepends=True)

        lines1 = [line if line.endswith("\n") else line + "\n" for line in lines1]
        lines2 = [line if line.endswith("\n") else line + "\n" for line in lines2]

        diff = difflib.unified_diff(
            lines1, lines2, fromfile=label1, tofile=label2, lineterm="", n=3
        )

        diff_lines = list(diff)

        if diff_lines and not diff_lines[0].startswith("==="):
            diff_lines.insert(2, "=" * 67 + "\n")

        result = "".join(diff_lines)

        max_chars = 2000
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(diff truncated)"

        return result.rstrip()

    @final
    @staticmethod
    def _parse_search_replace_blocks(content: str) -> list[SearchReplaceBlock]:
        """Parse SEARCH/REPLACE blocks from content.

        Supports multiple formats for model compatibility:
        1. Standard: <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE
        2. With code fences: ```...<<<<<<< SEARCH...```
        3. Simple separator: old_text\n=======\nnew_text (Gemma 4 style)
        4. JSON: {"old_string": "...", "new_string": "..."} or {"search": "...", "replace": "..."}
        """
        # Try standard format first
        matches = SEARCH_REPLACE_BLOCK_WITH_FENCE_RE.findall(content)
        if not matches:
            matches = SEARCH_REPLACE_BLOCK_RE.findall(content)

        if matches:
            return [
                SearchReplaceBlock(
                    search=search.rstrip("\r\n"), replace=replace.rstrip("\r\n")
                )
                for search, replace in matches
            ]

        # Fallback: try JSON format (some models send structured args)
        import json
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                old = data.get("old_string") or data.get("search") or data.get("old")
                new = data.get("new_string") or data.get("replace") or data.get("new")
                if old is not None and new is not None:
                    return [SearchReplaceBlock(search=str(old), replace=str(new))]
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: simple ======= separator (Gemma 4 often uses this)
        if "\n=======\n" in content:
            parts = content.split("\n=======\n", 1)
            if len(parts) == 2:
                search = parts[0].strip()
                replace = parts[1].strip()
                if search:
                    return [SearchReplaceBlock(search=search, replace=replace)]

        # Fallback: --- separator
        if "\n---\n" in content:
            parts = content.split("\n---\n", 1)
            if len(parts) == 2:
                search = parts[0].strip()
                replace = parts[1].strip()
                if search:
                    return [SearchReplaceBlock(search=search, replace=replace)]

        return []

    @final
    @staticmethod
    def _find_search_context(
        content: str, search_text: str, max_context: int = 5
    ) -> str:
        lines = content.split("\n")
        search_lines = search_text.split("\n")

        if not search_lines:
            return "Search text is empty"

        first_search_line = search_lines[0].strip()
        if not first_search_line:
            return "First line of search text is empty or whitespace only"

        matches = []
        for i, line in enumerate(lines):
            if first_search_line in line:
                matches.append(i)

        if not matches:
            return f"First search line '{first_search_line}' not found anywhere in file"

        context_lines = []
        for match_idx in matches[:3]:
            start = max(0, match_idx - max_context)
            end = min(len(lines), match_idx + max_context + 1)

            context_lines.append(f"\nPotential match area around line {match_idx + 1}:")
            for i in range(start, end):
                marker = ">>>" if i == match_idx else "   "
                context_lines.append(f"{marker} {i + 1:3d}: {lines[i]}")

        return "\n".join(context_lines)
