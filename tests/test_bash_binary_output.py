"""tool_bash must not choke on binary / non-UTF8 stdout. text=True with the
default (strict) decode raised UnicodeDecodeError INSIDE the reader thread — the
thread died and the agent got "(no output)", losing even the text parts of mixed
output. errors="replace" keeps it non-crashing and preserves the text. Found by
probing the binary-output I/O path directly."""
from __future__ import annotations

from drydock.tools import tool_bash


def _run(cmd):
    return tool_bash({"command": cmd, "timeout": 8}, {"cwd": "/tmp"})


def test_invalid_utf8_does_not_crash_and_keeps_text():
    out = _run(r"printf '\xff\xfe hello'")
    assert "hello" in out and "(no output)" not in out


def test_mixed_text_and_binary_preserves_text():
    # the readable lines must survive even though raw bytes sit between them
    out = _run(r"echo START; printf '\xc0\xc1\xff'; echo END")
    assert "START" in out and "END" in out


def test_binary_file_head_returns_something():
    out = _run("head -c 80 /bin/ls")
    assert out.strip() and "(no output)" not in out and "EXCEPTION" not in out


def test_plain_text_unaffected():
    out = _run("echo hello && echo world")
    assert "hello" in out and "world" in out
