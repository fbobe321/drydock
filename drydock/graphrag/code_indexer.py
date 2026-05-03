"""AST-based code symbol indexer — extracts class/function/method defs
with parent-class info and qualified names.

Scope choice: stdlib `ast` only. We don't try to evaluate annotations or
follow imports across files at index time — that's a v1 problem. What we
DO record is enough to mitigate pattern 4 from MODEL_SHORTCOMINGS:

    Q: where is `is_json` defined?
    A: werkzeug/wrappers/request.py:412 (method on Request class)

    Q: what does flask.wrappers.Request inherit from?
    A: ['werkzeug.wrappers.Request', 'JSONMixin']

The indexer walks .py files, extracts defs with `Visit*` AST visitors,
and yields `SymbolRecord` entries. Storage layer (`storage.py`) writes
these to SQLite.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# Files we never index (vendored, generated, junk).
_SKIP_DIR_NAMES = frozenset({
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
    "site-packages",  # avoid indexing installed packages
    ".eggs", "egg-info",
})

_MAX_FILE_BYTES = 1_000_000   # skip giant generated files


@dataclass
class SymbolRecord:
    """One indexed symbol — flat shape, ready to write to SQLite."""
    name: str
    qualname: str
    kind: str            # "class" | "function" | "method"
    file: str            # absolute path
    line: int            # 1-based
    end_line: int        # 1-based, inclusive
    parents: list[str] = field(default_factory=list)
    docstring: str = ""

    @property
    def citation_id(self) -> str:
        return f"{self.file}:{self.line}"


def index_path(root: str | Path) -> Iterator[SymbolRecord]:
    """Walk `root` recursively, yield a SymbolRecord per def found.

    `root` may be a single file or a directory. Symlinks and skip-dirs
    are pruned. Parse errors on individual files are swallowed (we yield
    nothing for that file and continue).
    """
    root = Path(root).resolve()
    if root.is_file() and root.suffix == ".py":
        yield from _index_file(root)
        return
    if not root.is_dir():
        return

    for path in _walk_python_files(root):
        try:
            yield from _index_file(path)
        except (SyntaxError, ValueError, UnicodeDecodeError):
            # Index what we can; skip what we can't parse.
            continue


def _walk_python_files(root: Path) -> Iterator[Path]:
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in _SKIP_DIR_NAMES or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.suffix == ".py" and entry.is_file():
                try:
                    if entry.stat().st_size > _MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield entry


def _index_file(path: Path) -> Iterator[SymbolRecord]:
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(path))
    module_qualname = _module_qualname_for(path)
    # Build the file's import alias map so base classes can be resolved
    # to their canonical qualname (`from werkzeug.wrappers import Request
    # as WR` → `WR -> werkzeug.wrappers.Request`).
    alias_map = _build_alias_map(tree)
    yield from _walk_node(tree, path, module_qualname, [], alias_map)


def _build_alias_map(tree: ast.AST) -> dict[str, str]:
    """Walk top-level `Import` / `ImportFrom` nodes and build the
    {local_name: canonical_qualname} map. Only top-level imports — we
    don't trace conditional/function-local imports (rare and usually
    not load-bearing for class hierarchies)."""
    alias_map: dict[str, str] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                # `import a.b.c` → local "a" → canonical "a.b.c"? No —
                # use the full dotted name as the canonical for `as`
                # cases, otherwise the bare name.
                canonical = alias.asname and alias.name or alias.name
                alias_map[local] = canonical
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                canonical = f"{module}.{alias.name}" if module else alias.name
                alias_map[local] = canonical
    return alias_map


def _walk_node(
    node: ast.AST,
    file: Path,
    module_qualname: str,
    qual_stack: list[str],
    alias_map: dict[str, str],
) -> Iterator[SymbolRecord]:
    """Recursive AST walk. `qual_stack` is the chain of enclosing class /
    function names so we can build qualified names like
    `Foo.bar.helper` for nested defs."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            inner_qual = qual_stack + [child.name]
            raw_parents = [_unparse(b) for b in child.bases]
            resolved = [alias_map.get(p, p) for p in raw_parents]
            yield SymbolRecord(
                name=child.name,
                qualname=_join_qual(module_qualname, inner_qual),
                kind="class",
                file=str(file),
                line=child.lineno,
                end_line=getattr(child, "end_lineno", child.lineno),
                parents=resolved,
                docstring=ast.get_docstring(child) or "",
            )
            yield from _walk_node(child, file, module_qualname, inner_qual, alias_map)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner_qual = qual_stack + [child.name]
            kind = "method" if (qual_stack and qual_stack[-1][0].isupper()) else "function"
            yield SymbolRecord(
                name=child.name,
                qualname=_join_qual(module_qualname, inner_qual),
                kind=kind,
                file=str(file),
                line=child.lineno,
                end_line=getattr(child, "end_lineno", child.lineno),
                parents=[],
                docstring=ast.get_docstring(child) or "",
            )
            yield from _walk_node(child, file, module_qualname, inner_qual, alias_map)
        else:
            yield from _walk_node(child, file, module_qualname, qual_stack, alias_map)


def _module_qualname_for(file: Path) -> str:
    """Best-effort module qualname. Uses `__init__.py` walks to detect
    packages; falls back to the bare stem if we can't tell."""
    parts: list[str] = [file.stem if file.stem != "__init__" else ""]
    parent = file.parent
    while parent != parent.parent:
        if (parent / "__init__.py").is_file():
            parts.insert(0, parent.name)
            parent = parent.parent
        else:
            break
    return ".".join(p for p in parts if p)


def _join_qual(module_qualname: str, parts: list[str]) -> str:
    chain = ".".join(parts)
    if module_qualname:
        return f"{module_qualname}.{chain}" if chain else module_qualname
    return chain


def _unparse(node: ast.AST) -> str:
    """Best-effort string rendering of an AST node (Python 3.9+ has
    ast.unparse). Used for base-class names."""
    try:
        return ast.unparse(node)
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            try:
                return f"{_unparse(node.value)}.{node.attr}"
            except Exception:
                return node.attr
        return "<unparseable>"
