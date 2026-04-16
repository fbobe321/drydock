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


def _check_stub_classes(tree, file_path: Path) -> list[str]:
    """Detect inline stub classes that should be real implementations.

    Catches the lang_interp anti-pattern: model "fixes" ModuleNotFoundError
    by defining empty placeholder classes inline in cli.py/__main__.py:

        class Interpreter:
            def run(self, ast):
                pass

        class REPL:
            def start(self):
                pass

    The stub silences the import error, `python3 -m pkg --help` works,
    but every real execution is a no-op. See MODEL_SHORTCOMINGS.md #10a.

    Returns a list of human-readable problem descriptions.
    """
    import ast as ast_mod
    import re

    pkg_dir = file_path.parent
    problems: list[str] = []

    def _is_stub_body(body) -> bool:
        """True if a function body is purely a stub (pass/…/return/raise NIE)."""
        # Skip leading docstring
        if (body and isinstance(body[0], ast_mod.Expr)
                and isinstance(body[0].value, ast_mod.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        if len(body) != 1:
            return False
        s = body[0]
        if isinstance(s, ast_mod.Pass):
            return True
        if (isinstance(s, ast_mod.Expr)
                and isinstance(s.value, ast_mod.Constant)
                and s.value.value is ...):
            return True
        if isinstance(s, ast_mod.Return):
            # return None / return / return <literal>
            if s.value is None:
                return True
            if isinstance(s.value, ast_mod.Constant):
                return True
        if isinstance(s, ast_mod.Raise):
            exc = s.exc
            if isinstance(exc, ast_mod.Name) and exc.id == "NotImplementedError":
                return True
            if (isinstance(exc, ast_mod.Call)
                    and isinstance(exc.func, ast_mod.Name)
                    and exc.func.id == "NotImplementedError"):
                return True
        return False

    for node in tree.body:
        if not isinstance(node, ast_mod.ClassDef):
            continue

        # Skip explicit abstract base classes / protocols / dataclasses
        is_abstract = any(
            (isinstance(b, ast_mod.Name) and b.id in {"ABC", "Protocol"}) or
            (isinstance(b, ast_mod.Attribute) and b.attr in {"ABC", "Protocol"})
            for b in node.bases
        )
        if is_abstract:
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast_mod.Name) and deco.id in {
                "dataclass", "runtime_checkable", "final"
            }:
                is_abstract = True
                break
            if isinstance(deco, ast_mod.Attribute) and deco.attr in {
                "dataclass", "runtime_checkable"
            }:
                is_abstract = True
                break
        if is_abstract:
            continue

        methods = [
            m for m in node.body
            if isinstance(m, (ast_mod.FunctionDef, ast_mod.AsyncFunctionDef))
        ]
        if not methods:
            continue
        # Look only at NON-dunder methods. A class is a stub when all of its
        # public methods are trivial, even if __init__ does real setup (storing
        # args on self). The lang_interp pattern frequently has a real __init__
        # plus stub `run`/`evaluate`/`start` methods.
        non_dunder = [m for m in methods if not (
            m.name.startswith("__") and m.name.endswith("__")
        )]
        if not non_dunder:
            continue
        if not all(_is_stub_body(m.body) for m in non_dunder):
            continue

        # All methods are stubs. Where should this class REALLY live?
        class_name_lower = node.name.lower()
        # CamelCase → snake_case, but keep acronyms together (REPL → repl,
        # HTTPServer → http_server, XMLParser → xml_parser).
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", node.name)
        snake = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", snake).lower()
        candidates = {class_name_lower, snake}

        # If this file IS the natural home for the class, skip — it might be
        # a genuine work-in-progress stub in its own module.
        if file_path.stem in candidates:
            continue

        # Is there a companion file where the real implementation could live?
        real_module = None
        for c in candidates:
            if (pkg_dir / f"{c}.py").exists():
                real_module = f"{c}.py"
                break
            if (pkg_dir / c / "__init__.py").exists():
                real_module = f"{c}/__init__.py"
                break

        if real_module:
            problems.append(
                f"class `{node.name}` (line {node.lineno}) has only stub methods "
                f"but {real_module} already exists in {pkg_dir.name}/. "
                f"Use `from .{real_module.split('/')[0].split('.')[0]} import {node.name}` "
                f"instead of redefining an empty stub."
            )
        elif file_path.stem in {
            "cli", "__main__", "app", "main", "server", "__init__"
        }:
            # Thin-wrapper files shouldn't contain real class implementations.
            problems.append(
                f"class `{node.name}` (line {node.lineno}) is defined INLINE in "
                f"{file_path.name} with only stub methods — every method body is "
                f"`pass`/`...`/`return`/`raise NotImplementedError`. "
                f"This silences ModuleNotFoundError but makes the class non-functional. "
                f"Write the REAL implementation in {sorted(candidates)[0]}.py and "
                f"import it here."
            )

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

        # Read-before-Write enforcement (Claude Code tool contract).
        # Only applies when overwrite=False — if the caller explicitly
        # passed overwrite=True (the default), honor that intent and do
        # the write. Blocking an explicit overwrite on "read it first"
        # caused an infinite loop in the 2026-04-16 stress run: a stale
        # 22-byte __init__.py from a prior test triggered the advisory,
        # and the model kept retrying write_file with overwrite=True —
        # each call returned bytes_written=0 with the same reminder,
        # the model never escaped, and drydock eventually died from
        # consecutive API errors. Per feedback_no_tool_errors_for_loop_detection.md
        # the safety net must not become its own loop source.
        read_state = ctx.read_file_state if ctx else None
        path_key = str(file_path)
        if file_existed and read_state is not None and not args.overwrite:
            prior = read_state.get(path_key)
            current_mtime = 0
            try:
                current_mtime = file_path.stat().st_mtime_ns
            except OSError:
                pass
            if prior is None:
                yield WriteFileResult(
                    path=path_key,
                    bytes_written=0,
                    file_existed=True,
                    content=(
                        "<system-reminder>\n"
                        f"{file_path.name} exists but you have not read it "
                        "this session. Read it first (read_file) so you can "
                        "see the current contents, then either:\n"
                        "  • edit with search_replace for targeted changes, "
                        "or\n"
                        "  • call write_file again with overwrite=true to "
                        "do a full rewrite.\n"
                        "This write was NOT applied to disk.\n"
                        "</system-reminder>"
                    ),
                )
                return
            if prior.get("timestamp") and current_mtime and current_mtime > prior["timestamp"]:
                yield WriteFileResult(
                    path=path_key,
                    bytes_written=0,
                    file_existed=True,
                    content=(
                        "<system-reminder>\n"
                        f"{file_path.name} was modified on disk since your "
                        "last read (mtime advanced). Re-read before writing "
                        "to avoid clobbering changes you haven't seen. "
                        "This write was NOT applied to disk.\n"
                        "</system-reminder>"
                    ),
                )
                return

        # Path-write thrash counter (just counts; advisory only). Per
        # feedback_no_tool_errors_for_loop_detection.md: loop detection
        # in tools must be advisory, never raise ToolError — hard blocks
        # cause their own loops on long tasks.
        path_writes = self.state.__dict__.setdefault("_path_writes", {})
        path_writes[path_key] = path_writes.get(path_key, 0) + 1
        path_n = path_writes[path_key]

        # Skip if file already has identical content (prevents write loops).
        # ADVISORY ONLY — must never raise ToolError. See
        # feedback_no_tool_errors_for_loop_detection.md: hard blocks cause
        # their own loops on long tasks.
        if file_existed:
            try:
                existing = file_path.read_text(encoding="utf-8")
                if existing == args.content:
                    state = self.state.__dict__.setdefault("_dup_writes", {})
                    key = str(file_path)
                    state[key] = state.get(key, 0) + 1
                    repeat_n = state[key]

                    body = "File already has this exact content (no-op). "
                    if repeat_n >= 2:
                        try:
                            siblings = sorted(
                                p.name for p in file_path.parent.iterdir()
                                if p.is_file() and not p.name.startswith("__pycache__")
                            )
                            sibling_str = ", ".join(siblings) if siblings else "(none)"
                            body += (
                                f"You've written this exact file {repeat_n} times. "
                                f"Files currently in {file_path.parent.name}/: "
                                f"{sibling_str}. Move on — write a DIFFERENT file "
                                f"from your plan, or run `bash` with `python3 -m "
                                f"{file_path.parent.name} --help` to verify."
                            )
                        except Exception:
                            body += "Move to the NEXT file in your plan."
                    else:
                        body += "Move to the NEXT file in your plan."
                    msg = f"<system-reminder>\n{body}\n</system-reminder>"

                    yield WriteFileResult(
                        path=str(file_path),
                        bytes_written=0,
                        file_existed=True,
                        content=msg,
                    )
                    return
            except Exception:
                pass

        # Injection guard: scan content for suspicious patterns
        from drydock.core.tools.injection_guard import check_content_for_injection
        if warning := check_content_for_injection(args.content, args.path):
            import logging
            logging.getLogger(__name__).warning("write_file: %s", warning)

        await self._write_file(args, file_path)

        # Auto-verify syntax for Python files
        # Also note heavy path-write counts so the model sees it's been
        # oscillating on the same file. Advisory-only — see
        # feedback_no_tool_errors_for_loop_detection.md.
        path_warning = ""
        if path_n >= 4:
            path_warning = (
                f"\n\nℹ You've written {file_path.name} {path_n} times "
                f"this session. If the file is oscillating between versions, "
                f"run your tests (`python3 -m {file_path.parent.name} --help` "
                f"or a bash command) to see the ACTUAL failure, then fix "
                f"one thing at a time with search_replace."
            )
        syntax_warning = ""
        if file_path.suffix == ".py":
            try:
                import ast
                tree = ast.parse(args.content)
            except SyntaxError as e:
                # Track consecutive syntax-error writes per file. ADVISORY
                # ONLY — must not raise ToolError. See
                # feedback_no_tool_errors_for_loop_detection.md.
                thrash = self.state.__dict__.setdefault("_syntax_thrash", {})
                key = str(file_path)
                thrash[key] = thrash.get(key, 0) + 1
                thrash_n = thrash[key]

                # Build surrounding-line context — the single error line
                # isn't enough for the model to pivot, it keeps rewriting
                # the whole file with the same structural mistake. Show
                # ±3 lines from the CONTENT JUST WRITTEN so the model can
                # see what it actually sent and do targeted SR on it.
                context_block = ""
                try:
                    src_lines = args.content.splitlines()
                    err_line = (e.lineno or 1) - 1
                    start = max(0, err_line - 3)
                    end = min(len(src_lines), err_line + 4)
                    numbered = [
                        f"  {'>' if i == err_line else ' '} {i+1:>4}: {src_lines[i]}"
                        for i in range(start, end)
                    ]
                    context_block = "\n" + "\n".join(numbered)
                except Exception:
                    pass

                syntax_warning = (
                    f"\n\n⚠ SYNTAX ERROR in {file_path.name} line {e.lineno}: {e.msg}"
                    f"{context_block}"
                    f"\n  Fix this before moving to the next file."
                )
                if thrash_n >= 3:
                    syntax_warning += (
                        f"\n  [{thrash_n}th consecutive syntax error on this "
                        f"file. Switch tactic: read_file() to see current "
                        f"state, then use search_replace for surgical fixes, "
                        f"or write a DIFFERENT file and come back later.]"
                    )
                elif thrash_n == 2:
                    syntax_warning += (
                        f"\n  [2nd syntax error on this file — consider "
                        f"read_file + search_replace on the lines shown "
                        f"above instead of another full rewrite.]"
                    )
            else:
                # Reset thrash counter on a successful parse.
                thrash = self.state.__dict__.get("_syntax_thrash", {})
                thrash.pop(str(file_path), None)
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

                # Catch stub-class anti-pattern (MODEL_SHORTCOMINGS #10a).
                # Model writes `class Interpreter: def run(self): pass` inline
                # in cli.py to silence ModuleNotFoundError instead of writing
                # interpreter.py. --help works but every execution is a no-op.
                stub_problems = _check_stub_classes(tree, file_path)
                if stub_problems:
                    stub_warning = (
                        f"\n\n⚠ {file_path.name} contains STUB classes "
                        f"(placeholder implementations that silence imports "
                        f"but do nothing):\n"
                        + "\n".join(f"  • {p}" for p in stub_problems[:3])
                        + "\n  Stub classes make the package look functional "
                        + "(`python3 -m pkg --help` works) but every real "
                        + "execution is a no-op. Write the REAL implementation."
                    )
                    if syntax_warning:
                        syntax_warning += stub_warning
                    else:
                        syntax_warning = stub_warning

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

        # Update read_file_state so subsequent write_file / search_replace
        # calls on this path pass the Read-before-Write check without
        # requiring an explicit re-read. We just wrote it, so we know
        # what's on disk.
        if read_state is not None:
            try:
                new_mtime = file_path.stat().st_mtime_ns
            except OSError:
                new_mtime = 0
            read_state[path_key] = {
                "content": args.content,
                "timestamp": new_mtime,
                "offset": 0,
                "limit": None,
            }

        # Terse success message (Claude Code pattern). Do NOT echo
        # args.content back — the model already has the content in its
        # assistant-message history. Echoing wastes context AND gives the
        # model an easy path to re-read what it just wrote instead of
        # moving on (one of the oscillation triggers). If there's a
        # warning/advisory, surface that INSTEAD of the content echo.
        action = "updated" if file_existed else "created"
        success_msg = (
            f"File {file_path.name} {action} successfully ({content_bytes} bytes)."
        )
        result = WriteFileResult(
            path=str(file_path),
            bytes_written=content_bytes,
            file_existed=file_existed,
            content=success_msg,
        )
        if syntax_warning or path_warning:
            combined = (syntax_warning or "") + (path_warning or "")
            result.content = (
                f"<system-reminder>{combined}\n</system-reminder>"
            )
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
