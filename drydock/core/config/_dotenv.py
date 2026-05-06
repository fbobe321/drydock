"""Minimal .env file reader/writer — replaces python-dotenv.

Supports the subset drydock actually uses:
  dotenv_values(path)           → dict[str, str | None]
  set_key(path, key, value)     → None
  unset_key(path, key)          → None

Format handled:
  KEY=VALUE          unquoted value
  KEY="VALUE"        double-quoted value (backslash escapes respected)
  KEY='VALUE'        single-quoted value (no escapes)
  KEY=               empty string
  # comment          ignored
  (blank line)       preserved by set_key/unset_key
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Union

_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:export\s+)?          # optional 'export '
    ([A-Za-z_][A-Za-z0-9_]*)  # key
    \s*=\s*
    (                       # value group
        '(?:[^'\\]|\\.)*'   # single-quoted
        |"(?:[^"\\]|\\.)*"  # double-quoted
        |[^#\r\n]*          # unquoted (up to comment or EOL)
    )?
    (?:\s*\#.*)?            # optional trailing comment
    $
    """,
    re.VERBOSE,
)


def _unescape_double(s: str) -> str:
    return s.encode("raw_unicode_escape").decode("unicode_escape")


def _parse_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        inner = raw[1:-1]
        return _unescape_double(inner)
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1]
    return raw


def dotenv_values(path: Union[str, Path]) -> dict[str, str | None]:
    """Read a .env file and return a dict of key→value pairs."""
    path = Path(path)
    result: dict[str, str | None] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m:
            result[m.group(1)] = _parse_value(m.group(2))
    return result


def set_key(
    path: Union[str, Path],
    key: str,
    value: str,
    quote_mode: str = "auto",
    export: bool = False,
) -> None:
    """Write or update KEY=VALUE in a .env file.

    If the key already exists, its line is updated in place.
    Otherwise the entry is appended.  The file is created if absent.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Choose quoting: always double-quote so spaces and special chars are safe.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key}="{escaped}"'

    if not path.exists():
        path.write_text(new_line + "\n", encoding="utf-8")
        return

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    key_re = re.compile(
        r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*="
    )
    replaced = False
    out: list[str] = []
    for line in lines:
        if not replaced and key_re.match(line):
            out.append(new_line + "\n")
            replaced = True
        else:
            out.append(line)

    if not replaced:
        # Ensure file ends with a newline before appending.
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(new_line + "\n")

    path.write_text("".join(out), encoding="utf-8")


def unset_key(
    path: Union[str, Path],
    key: str,
) -> None:
    """Remove KEY=... line from a .env file. No-op if file or key is absent."""
    path = Path(path)
    if not path.exists():
        return

    key_re = re.compile(
        r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*="
    )
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out = [line for line in lines if not key_re.match(line)]
    path.write_text("".join(out), encoding="utf-8")
