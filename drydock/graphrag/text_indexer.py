"""Text indexer — chunk markdown / prose into retrievable units.

Scope choice for v0: TF-IDF + cosine, all in pure Python. Zero external
ML dependencies. Good enough to validate the retrieval interface and
real failure-mode mitigations (pattern 10c — design memory across
sessions). Sentence-transformer / embedding upgrade is a v1 concern,
slot-in via the same `Retriever` interface.

Chunking strategy:
- Markdown: split on h1/h2/h3 boundaries. Within a section, further
  break on paragraph boundaries if a section > MAX_CHUNK_CHARS.
- Plain text: split on blank-line paragraphs; sliding window if chunk
  exceeds MAX_CHUNK_CHARS.
- Code (.py): handled by code_indexer.py (this module ignores .py).

Each chunk carries its source file + start/end lines so the harness
can require citations and an evaluator can verify them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


_TEXT_EXTENSIONS = frozenset({
    ".md", ".markdown", ".rst", ".txt",
})

_SKIP_DIR_NAMES = frozenset({
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
    "site-packages", ".eggs",
})

_MAX_FILE_BYTES = 2_000_000
MAX_CHUNK_CHARS = 1500
MIN_CHUNK_CHARS = 80          # below this, merge into next chunk


@dataclass
class TextChunk:
    """One indexed chunk — flat shape, ready to write to SQLite."""
    file: str             # absolute path
    start_line: int       # 1-based, inclusive
    end_line: int         # 1-based, inclusive
    content: str          # raw chunk text (incl. heading if md)

    @property
    def citation_id(self) -> str:
        return f"{self.file}:{self.start_line}-{self.end_line}"


def index_path(root: str | Path) -> Iterator[TextChunk]:
    """Walk `root`, yielding TextChunk per chunk found.

    `root` may be a file or directory. Skips python files (the code
    indexer handles those) and binary/oversized files. Parse errors
    are swallowed.
    """
    root = Path(root).resolve()
    if root.is_file():
        if _is_text_file(root):
            yield from _chunk_file(root)
        return
    if not root.is_dir():
        return

    for path in _walk_text_files(root):
        try:
            yield from _chunk_file(path)
        except (UnicodeDecodeError, OSError):
            continue


def _walk_text_files(root: Path) -> Iterator[Path]:
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
            elif entry.is_file() and _is_text_file(entry):
                try:
                    if entry.stat().st_size > _MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield entry


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _chunk_file(path: Path) -> Iterator[TextChunk]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return
    if path.suffix.lower() in (".md", ".markdown"):
        yield from _chunk_markdown(path, text)
    else:
        yield from _chunk_plain(path, text)


_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _chunk_markdown(path: Path, text: str) -> Iterator[TextChunk]:
    """Section-based chunking for markdown. Each h1/h2/h3 starts a new
    chunk; sections that exceed MAX_CHUNK_CHARS are sub-chunked on
    paragraph boundaries."""
    lines = text.splitlines()
    # Build section spans by finding header line indices.
    header_indices: list[int] = []
    for i, line in enumerate(lines):
        if _HEADER_RE.match(line):
            header_indices.append(i)

    if not header_indices:
        yield from _chunk_plain(path, text)
        return

    # Force a virtual end so the last section gets emitted.
    header_indices.append(len(lines))
    # Optionally include any preamble before first header.
    if header_indices[0] > 0:
        preamble = "\n".join(lines[: header_indices[0]]).strip()
        if len(preamble) >= MIN_CHUNK_CHARS:
            yield from _maybe_split(path, 1, header_indices[0], preamble)

    for idx in range(len(header_indices) - 1):
        start = header_indices[idx]
        end = header_indices[idx + 1]
        section = "\n".join(lines[start:end]).strip()
        if not section:
            continue
        yield from _maybe_split(path, start + 1, end, section)


def _chunk_plain(path: Path, text: str) -> Iterator[TextChunk]:
    """Paragraph-based chunking for plain text."""
    # Track 1-based line numbers for each paragraph.
    paragraphs: list[tuple[int, int, str]] = []
    cur_lines: list[str] = []
    cur_start: int = 1
    line_no = 0
    for line in text.splitlines():
        line_no += 1
        if line.strip():
            if not cur_lines:
                cur_start = line_no
            cur_lines.append(line)
        else:
            if cur_lines:
                paragraphs.append(
                    (cur_start, line_no - 1, "\n".join(cur_lines))
                )
                cur_lines = []
    if cur_lines:
        paragraphs.append((cur_start, line_no, "\n".join(cur_lines)))

    # Pack paragraphs into chunks <= MAX_CHUNK_CHARS, merging tiny ones.
    buf: list[tuple[int, int, str]] = []
    buf_chars = 0
    for start, end, body in paragraphs:
        if buf_chars + len(body) > MAX_CHUNK_CHARS and buf:
            yield _emit(path, buf)
            buf = []
            buf_chars = 0
        buf.append((start, end, body))
        buf_chars += len(body) + 2
    if buf:
        yield _emit(path, buf)


def _maybe_split(
    path: Path,
    start_line: int,
    end_line: int,
    content: str,
) -> Iterator[TextChunk]:
    """Yield content as one chunk if it fits, else split on paragraph."""
    if len(content) <= MAX_CHUNK_CHARS:
        yield TextChunk(
            file=str(path),
            start_line=start_line,
            end_line=end_line,
            content=content,
        )
        return
    # Sub-chunk on blank-line boundaries.
    paragraphs = re.split(r"\n\s*\n", content)
    cur: list[str] = []
    cur_chars = 0
    sub_start = start_line
    line_cursor = start_line
    for para in paragraphs:
        para_lines = para.count("\n") + 1
        if cur_chars + len(para) > MAX_CHUNK_CHARS and cur:
            chunk_end = line_cursor - 1
            yield TextChunk(
                file=str(path),
                start_line=sub_start,
                end_line=chunk_end,
                content="\n\n".join(cur),
            )
            cur = []
            cur_chars = 0
            sub_start = line_cursor
        cur.append(para)
        cur_chars += len(para) + 2
        line_cursor += para_lines + 1
    if cur:
        yield TextChunk(
            file=str(path),
            start_line=sub_start,
            end_line=end_line,
            content="\n\n".join(cur),
        )


def _emit(path: Path, buf: list[tuple[int, int, str]]) -> TextChunk:
    return TextChunk(
        file=str(path),
        start_line=buf[0][0],
        end_line=buf[-1][1],
        content="\n\n".join(b[2] for b in buf),
    )
