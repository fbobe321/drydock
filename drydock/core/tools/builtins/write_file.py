from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import ClassVar, final

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


def _check_main_module_entry(tree) -> str | None:
    """Detect the 'imports main but never calls it' bug in __main__.py files.

    Returns a description of the problem, or None if the file looks OK.

    Specifically catches this pattern:

        from pkg.cli import main
        # no call to main() anywhere

    Running `python3 -m pkg` then exits 0 with no output.
    """
    import ast

    ENTRY_CANDIDATES = {"main", "cli", "run", "app", "entry", "entry_point"}

    # Pass 1: collect entry-function names imported or defined at module level
    entry_names: set[str] = set()
    has_name_main_guard = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if name in ENTRY_CANDIDATES:
                    entry_names.add(name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[-1]
                if name in ENTRY_CANDIDATES:
                    entry_names.add(name)
        elif isinstance(node, ast.FunctionDef):
            if node.name in ENTRY_CANDIDATES:
                entry_names.add(node.name)
        elif isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                has_name_main_guard = True

    if not entry_names:
        return None

    # Pass 2: walk the entire tree and look for ANY call that references an
    # entry name — either `main()`, `cli.main()`, or `sys.exit(main())`, etc.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Collect all Name identifiers anywhere inside this Call expression.
        # This catches sys.exit(main()), sys.exit(cli()), cli.main(), etc.
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in entry_names:
                return None
            if isinstance(child, ast.Attribute) and child.attr in entry_names:
                return None

    if has_name_main_guard:
        return (
            f"imports {sorted(entry_names)} but the "
            f"`if __name__ == \"__main__\":` block does not call any of them"
        )
    return f"imports {sorted(entry_names)} but never calls any of them"


def _check_bare_raise_outside_except(tree) -> list[str]:
    """Detect `raise` without an argument outside of an except block.

    Bare `raise` is only valid inside an except handler (re-raises the
    active exception). Outside an except, it fails at runtime with
    'No active exception to reraise'. This is a common Gemma 4 mistake:
    writing `if not found: raise` when `raise SomeError(...)` was intended.

    Returns a list of line-number hints for each problem found.
    """
    import ast

    problems: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self.in_except = 0
            self.in_func = 0

        def visit_ExceptHandler(self, node):
            self.in_except += 1
            self.generic_visit(node)
            self.in_except -= 1

        def visit_FunctionDef(self, node):
            self.in_func += 1
            # bare raise inside a function but outside except is still bad
            # (the function can be called from anywhere, not just except).
            self.generic_visit(node)
            self.in_func -= 1

        def visit_AsyncFunctionDef(self, node):
            self.visit_FunctionDef(node)

        def visit_Raise(self, node):
            if node.exc is None and self.in_except == 0:
                problems.append(
                    f"line {node.lineno}: bare `raise` outside any except block "
                    f"(will fail with 'No active exception to reraise' at runtime)"
                )
            # Don't recurse — raise has no sub-Raise nodes
            self.generic_visit(node)

    _Visitor().visit(tree)
    return problems


def _check_missing_sibling_imports(tree, file_path: Path) -> set[str]:
    """Detect imports of sibling modules that don't exist on disk yet.

    Returns a set of missing module names (without .py extension).

    Catches the minivc-style bug where __init__.py says
    `from .cli import CLI` but cli.py was never written.

    Only checks RELATIVE imports (`.x`) and absolute imports of the same
    package (`pkg.x` where pkg is the directory name). Other imports
    (stdlib, third-party) are ignored.
    """
    import ast

    pkg_dir = file_path.parent
    pkg_name = pkg_dir.name
    if not pkg_name:
        return set()

    missing: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        # Resolve module: relative `.x` or absolute `pkg.x`
        if node.level == 1:
            # `from .x import Y` — relative within current package
            sub = node.module or ""
        elif node.level == 0 and node.module and node.module.startswith(pkg_name + "."):
            # `from pkg.x import Y` — absolute, same package
            sub = node.module[len(pkg_name) + 1 :]
        else:
            continue

        if not sub:
            continue
        # Take the first segment: `pkg.sub.deep` → `sub`
        first = sub.split(".")[0]
        if not first or first == "__init__":
            continue

        # Check whether `pkg/<first>.py` or `pkg/<first>/__init__.py` exists
        candidate_module = pkg_dir / f"{first}.py"
        candidate_pkg = pkg_dir / first / "__init__.py"
        if not candidate_module.exists() and not candidate_pkg.exists():
            missing.add(first)

    return missing


class WriteFileArgs(BaseModel):
    path: str
    content: str
    overwrite: bool = Field(
        default=True, description="Whether to overwrite an existing file."
    )


class WriteFileResult(BaseModel):
    path: str
    bytes_written: int
    file_existed: bool
    content: str


class WriteFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    max_write_bytes: int = 64_000
    create_parent_dirs: bool = True


class WriteFile(
    BaseTool[WriteFileArgs, WriteFileResult, WriteFileConfig, BaseToolState],
    ToolUIData[WriteFileArgs, WriteFileResult],
):
    description: ClassVar[str] = (
        "Create or overwrite a UTF-8 file. Fails if file exists unless 'overwrite=True'."
    )

    @classmethod
    def format_call_display(cls, args: WriteFileArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Writing {args.path}{' (overwrite)' if args.overwrite else ''}",
            content=args.content,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, WriteFileResult):
            action = "Overwritten" if event.result.file_existed else "Created"
            return ToolResultDisplay(
                success=True, message=f"{action} {Path(event.result.path).name}"
            )

        return ToolResultDisplay(success=True, message="File written")

    @classmethod
    def get_status_text(cls) -> str:
        return "Writing file"

    def resolve_permission(self, args: WriteFileArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @final
    async def run(
        self, args: WriteFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WriteFileResult, None]:
        file_path, file_existed, content_bytes = self._prepare_and_validate_path(args)

        # Skip if file already has identical content (prevents write loops).
        #
        # Three-tier escalation — first call is a friendly advisory, second
        # adds concrete state, third is a HARD BLOCK via ToolError. Every
        # advisory signal in drydock (dedup content, system note nudge,
        # missing-import warning, user-typed STOP interrupt) is ignored by
        # Gemma 4 on write loops — confirmed by 3 of 5 failures in the
        # April 2026 user-pain suite run. The hard block is narrow: it only
        # fires on pure no-op writes (file exists AND content matches disk)
        # after 3 identical attempts. Legitimate retries (different content,
        # different path, or after the model successfully wrote a DIFFERENT
        # file) are never blocked.
        if file_existed:
            try:
                existing = file_path.read_text(encoding="utf-8")
                if existing == args.content:
                    state = self.state.__dict__.setdefault("_dup_writes", {})
                    key = str(file_path)
                    state[key] = state.get(key, 0) + 1
                    repeat_n = state[key]

                    # Tier 3: hard block. 3rd identical no-op write → ToolError.
                    if repeat_n >= 3:
                        try:
                            siblings = sorted(
                                p.name for p in file_path.parent.iterdir()
                                if p.is_file() and not p.name.startswith("__pycache__")
                            )
                            sibling_str = ", ".join(siblings) if siblings else "(none)"
                        except Exception:
                            sibling_str = "(unknown)"
                        raise ToolError(
                            f"BLOCKED: write_file({file_path.name}) has been called "
                            f"{repeat_n} times with IDENTICAL content that already "
                            f"matches the file on disk. This is a no-op loop. "
                            f"Files in {file_path.parent.name}/: {sibling_str}. "
                            f"Do something DIFFERENT — write a different file from "
                            f"your plan, or run bash with `python3 -m "
                            f"{file_path.parent.name} --help` to verify the package. "
                            f"This exact write_file call is refused."
                        )

                    # Tier 2: escalated advisory with directory listing.
                    msg = "File already has this exact content (no-op). "
                    if repeat_n >= 2:
                        try:
                            siblings = sorted(
                                p.name for p in file_path.parent.iterdir()
                                if p.is_file() and not p.name.startswith("__pycache__")
                            )
                            sibling_str = ", ".join(siblings) if siblings else "(none)"
                            msg += (
                                f"You've written this exact file {repeat_n} times. "
                                f"Files currently in {file_path.parent.name}/: "
                                f"{sibling_str}. STOP writing this file. "
                                f"Either write a DIFFERENT file from your plan, "
                                f"or run `bash` with `python3 -m {file_path.parent.name} --help` "
                                f"to verify the package works. "
                                f"ONE MORE identical write to this path will be blocked."
                            )
                        except Exception:
                            msg += "Move to the NEXT file in your plan."
                    else:
                        # Tier 1: first duplicate — friendly advisory.
                        msg += "Move to the NEXT file in your plan."

                    yield WriteFileResult(
                        path=str(file_path),
                        bytes_written=0,
                        file_existed=True,
                        content=msg,
                    )
                    return
            except ToolError:
                raise
            except Exception:
                pass

        # Injection guard: scan content for suspicious patterns
        from drydock.core.tools.injection_guard import check_content_for_injection
        if warning := check_content_for_injection(args.content, args.path):
            import logging
            logging.getLogger(__name__).warning("write_file: %s", warning)

        await self._write_file(args, file_path)

        # Auto-verify syntax for Python files
        syntax_warning = ""
        if file_path.suffix == ".py":
            try:
                import ast
                tree = ast.parse(args.content)
            except SyntaxError as e:
                syntax_warning = (
                    f"\n\n⚠ SYNTAX ERROR in {file_path.name} line {e.lineno}: {e.msg}"
                    f"\n  {e.text.rstrip() if e.text else ''}"
                    f"\n  Fix this before moving to the next file."
                )
            else:
                # Catch silent-exit __main__.py: imports a `main` function but
                # never actually calls it. Real bug found in the codec build —
                # `python3 -m pkg` ran, exited 0, and produced no output.
                if file_path.name == "__main__.py":
                    entry_warning = _check_main_module_entry(tree)
                    if entry_warning:
                        syntax_warning = (
                            f"\n\n⚠ {file_path.name} looks broken: {entry_warning}"
                            f"\n  Add `if __name__ == \"__main__\": main()` at the "
                            f"bottom, or call the entry function at module level. "
                            f"Without it, `python3 -m {file_path.parent.name}` will "
                            f"exit silently with no output."
                        )
                # Catch bare `raise` outside except. Found in ACE v2 build —
                # drydock wrote `if not found: raise` in cli.py, which fails
                # at runtime with "No active exception to reraise".
                bare_raises = _check_bare_raise_outside_except(tree)
                if bare_raises:
                    raise_warning = (
                        f"\n\n⚠ {file_path.name} has bare `raise` outside of "
                        f"any except block:\n"
                        + "\n".join(f"  {p}" for p in bare_raises[:3])
                        + "\n  A bare `raise` only works inside an `except` handler. "
                        + "Use `raise SpecificError(...)` with an explicit exception, "
                        + "or return/print an error instead."
                    )
                    if syntax_warning:
                        syntax_warning += raise_warning
                    else:
                        syntax_warning = raise_warning

                # Catch missing-import bug: file imports `from .x import Y` or
                # `from pkg.x import Y` but x.py doesn't exist on disk yet.
                # Real bug found in minivc build — __init__.py imported `cli`
                # but the model never wrote cli.py, so the package was unimportable.
                missing = _check_missing_sibling_imports(tree, file_path)
                if missing:
                    if syntax_warning:
                        syntax_warning += (
                            f"\n\nALSO: {file_path.name} imports modules that don't "
                            f"exist yet: {sorted(missing)}. Write these files next."
                        )
                    else:
                        syntax_warning = (
                            f"\n\n⚠ {file_path.name} imports modules that don't "
                            f"exist yet on disk: {sorted(missing)}.\n"
                            f"  You MUST also write these files before running the "
                            f"package, otherwise `import {file_path.parent.name}` "
                            f"will raise ModuleNotFoundError."
                        )

        result = WriteFileResult(
            path=str(file_path),
            bytes_written=content_bytes,
            file_existed=file_existed,
            content=args.content,
        )
        if syntax_warning:
            result.content = syntax_warning  # Override content with the warning
        yield result

    _BINARY_EXTENSIONS = frozenset({
        ".pptx", ".xlsx", ".docx", ".pdf", ".zip", ".tar", ".gz", ".bz2",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
        ".mp3", ".mp4", ".wav", ".avi", ".mov",
        ".exe", ".dll", ".so", ".dylib", ".whl", ".egg",
        ".sqlite", ".db", ".pkl", ".pickle", ".npy", ".npz",
    })

    def _prepare_and_validate_path(self, args: WriteFileArgs) -> tuple[Path, bool, int]:
        if not args.path.strip():
            raise ToolError("Path cannot be empty")

        # Warn about binary file extensions
        ext = Path(args.path).suffix.lower()
        if ext in self._BINARY_EXTENSIONS:
            raise ToolError(
                f"write_file creates UTF-8 text files only. "
                f"'{ext}' is a binary format — use bash to run a Python script instead. "
                f"Example: write a .py script that uses the appropriate library "
                f"(python-pptx, openpyxl, Pillow, etc.), then run it with bash."
            )

        content_bytes = len(args.content.encode("utf-8"))
        if content_bytes > self.config.max_write_bytes:
            raise ToolError(
                f"Content exceeds {self.config.max_write_bytes} bytes limit"
            )

        file_path = Path(args.path).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        file_path = file_path.resolve()

        file_existed = file_path.exists()

        if file_existed and not args.overwrite:
            raise ToolError(
                f"File '{file_path}' exists. Set overwrite=True to replace."
            )

        if self.config.create_parent_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        elif not file_path.parent.exists():
            raise ToolError(f"Parent directory does not exist: {file_path.parent}")

        return file_path, file_existed, content_bytes

    async def _write_file(self, args: WriteFileArgs, file_path: Path) -> None:
        import asyncio
        import concurrent.futures

        def _sync_write():
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(args.content)

        try:
            # Use a thread with a real timeout — anyio.fail_after doesn't
            # cancel thread-pool I/O, which is why writes hung for 45+ minutes
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, _sync_write),
                timeout=10,  # 10 seconds is generous for a text file write
            )
        except asyncio.TimeoutError:
            raise ToolError(
                f"Timed out writing {file_path} after 10s. "
                f"The file may be locked by another process."
            )
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e
