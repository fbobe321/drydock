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


def write_warnings(path: str, content: str) -> list[str]:
    """All advisory warnings for a freshly written file (order: syntax first).
    Syntax error short-circuits the rest (they'd be noise)."""
    syntax = python_syntax_warning(path, content)
    if syntax:
        return [syntax]
    out = []
    for w in (main_entry_warning(path, content), stub_warning(path, content)):
        if w:
            out.append(w)
    return out
