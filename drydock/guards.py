"""Advisory write/edit guards.

After a file is written or edited, Drydock runs a few cheap checks and, if
something looks broken, *appends a warning* to the tool result. These are
advisory — the write still happens — so the model gets immediate feedback
("you just wrote a file with a syntax error") instead of discovering it
three tool calls later. Guards never block a write and never raise.

All logic original to Drydock.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


def _is_python(path: str) -> bool:
    return path.endswith(".py")


def python_syntax_warning(path: str, content: str) -> str | None:
    """Warn if a Python file does not parse."""
    if not _is_python(path) or not content.strip():
        return None
    try:
        ast.parse(content)
    except SyntaxError as e:
        line = e.lineno or "?"
        return (
            f"[WARNING: {path} has a Python syntax error at line {line}: "
            f"{e.msg}. Fix it before continuing.]"
        )
    return None


def main_entry_warning(path: str, content: str) -> str | None:
    """Warn if `if __name__ == '__main__':` calls a name defined nowhere in
    the file (a common 'wrote the entrypoint but not the function' mistake).
    """
    if not _is_python(path):
        return None
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None  # syntax warning already covers this

    defined: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)

    # find the __main__ guard block and the simple calls inside it
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If)):
            continue
        src = ast.dump(node.test)
        if "__name__" not in src or "__main__" not in src:
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                name = call.func.id
                if name not in defined and name not in imported and name not in dir(__builtins__):
                    return (
                        f"[WARNING: {path} calls {name}() under "
                        f"`if __name__ == '__main__'` but {name} is not defined "
                        f"or imported in this file.]"
                    )
    return None


_STUB_RE = re.compile(r":\s*(?:\.\.\.|pass)\s*$", re.MULTILINE)


def stub_warning(path: str, content: str) -> str | None:
    """Warn if a Python file looks like nothing but stubs (bodies that are
    only `pass` or `...`), which usually means the model wrote a skeleton and
    forgot to implement it.
    """
    if not _is_python(path) or not content.strip():
        return None
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(funcs) < 2:
        return None

    def is_stub(fn) -> bool:
        body = fn.body
        if len(body) == 1:
            s = body[0]
            if isinstance(s, ast.Pass):
                return True
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) \
                    and s.value.value is Ellipsis:
                return True
        # docstring + nothing else counts as a stub too
        if len(body) == 1 and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            return True
        return False

    if all(is_stub(f) for f in funcs):
        return (
            f"[WARNING: every function in {path} is an empty stub "
            f"(pass/.../docstring only). Implement the bodies before relying "
            f"on this file.]"
        )
    return None


# Git/SEARCH-REPLACE conflict markers at the start of a line. We key on the
# 7-angle-bracket markers (essentially never legitimate file content) and NOT
# on `=======`, which appears in decorative comment dividers.
_CONFLICT_MARKER_RE = re.compile(r"^(?:<{7}|>{7})", re.MULTILINE)


def has_conflict_markers(content: str) -> bool:
    """True if content contains git / SEARCH-REPLACE conflict markers.

    Gemma sometimes emits a malformed edit block as raw file content; writing
    `<<<<<<< SEARCH` / `>>>>>>> REPLACE` verbatim corrupts the file and triggers
    a retry loop. Ported from v2's search_replace conflict-marker guard."""
    return bool(content) and bool(_CONFLICT_MARKER_RE.search(content))


def conflict_marker_refusal(path: str) -> str:
    """The string returned in place of writing conflict-marker content."""
    return (
        f"REFUSED: the content for {path} contains conflict markers "
        f"(<<<<<<< / >>>>>>>). That looks like a malformed edit block, not real "
        f"file content — writing it would corrupt the file. Send the final file "
        f"content (or a clean Edit with old_string/new_string), without any "
        f"<<<<<<< SEARCH / ======= / >>>>>>> REPLACE markers."
    )


def sibling_imports_warning(path: str, content: str) -> str | None:
    """Warn when a file imports a sibling module that doesn't exist on disk yet.

    Catches the classic "wrote __init__.py with `from .cli import CLI` but never
    wrote cli.py" bug. Only RELATIVE imports (`.x`) and absolute imports of the
    SAME package (`pkg.x`, where pkg is the containing directory) are checked;
    stdlib/third-party imports are ignored. Ported from v2's
    _check_missing_sibling_imports, where it was earned from real package builds.
    """
    if not _is_python(path) or not content.strip():
        return None
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None  # syntax warning already covers this

    pkg_dir = Path(path).parent
    pkg_name = pkg_dir.name
    if not pkg_name:
        return None

    missing: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 1:                      # from .x import Y
            sub = node.module or ""
        elif (node.level == 0 and node.module    # from pkg.x import Y
              and node.module.startswith(pkg_name + ".")):
            sub = node.module[len(pkg_name) + 1:]
        else:
            continue
        first = sub.split(".")[0]
        if not first or first == "__init__":
            continue
        if not (pkg_dir / f"{first}.py").exists() \
                and not (pkg_dir / first / "__init__.py").exists():
            missing.add(first)

    if not missing:
        return None
    names = ", ".join(sorted(missing))
    example = sorted(missing)[0]
    return (
        f"[WARNING: {path} imports sibling module(s) that don't exist yet: "
        f"{names}. Create them (e.g. {pkg_name}/{example}.py) before relying on "
        f"this import.]"
    )


def bare_raise_warning(path: str, content: str) -> str | None:
    """Warn on a bare `raise` (no exception) outside any except block.

    A bare `raise` re-raises the active exception and is only valid inside an
    except handler; elsewhere it fails at runtime with 'No active exception to
    reraise'. A common Gemma mistake: `if not found: raise` where
    `raise SomeError(...)` was intended. Ported from v2. Advisory only.
    """
    if not _is_python(path) or not content.strip():
        return None
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    problems: list[int] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.in_except = 0

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            self.in_except += 1
            self.generic_visit(node)
            self.in_except -= 1

        def visit_Raise(self, node: ast.Raise) -> None:
            if node.exc is None and self.in_except == 0:
                problems.append(node.lineno)
            self.generic_visit(node)

    _Visitor().visit(tree)
    if not problems:
        return None
    lines = ", ".join(str(n) for n in problems)
    return (
        f"[WARNING: {path} has a bare `raise` outside any except block "
        f"(line {lines}). It will fail at runtime with 'No active exception to "
        f"reraise' — raise a specific exception, e.g. raise ValueError(...).]"
    )


def write_warnings(path: str, content: str) -> list[str]:
    """All advisory warnings for a freshly written file (order: syntax first).
    Syntax error short-circuits the rest (they'd be noise)."""
    syntax = python_syntax_warning(path, content)
    if syntax:
        return [syntax]
    out = []
    for w in (
        main_entry_warning(path, content),
        stub_warning(path, content),
        sibling_imports_warning(path, content),
        bare_raise_warning(path, content),
    ):
        if w:
            out.append(w)
    return out
