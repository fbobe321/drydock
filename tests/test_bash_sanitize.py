"""Bash output is sanitized before the model sees it: ANSI escape sequences
(colour/cursor control some tools emit even to a pipe) are stripped, and NUL
bytes (which trip some LLM servers' JSON handling) are dropped. Text/tabs/
newlines/Unicode are preserved. Found by probing the output I/O path."""
from __future__ import annotations

import pytest

from drydock.tools import tool_bash, _sanitize_bash_output


@pytest.mark.parametrize("raw,clean", [
    ("\x1b[31mRED\x1b[0m normal", "RED normal"),   # SGR colour
    ("\x1b[2K\x1b[1Gline", "line"),                 # cursor control
    ("\x1b]0;window title\x07keep", "keep"),        # OSC title
    ("a\x00b\x00c", "abc"),                          # NUL bytes
    ("plain\ttext\nline2 🚢⚓", "plain\ttext\nline2 🚢⚓"),  # preserved
])
def test_sanitizer(raw, clean):
    assert _sanitize_bash_output(raw) == clean


def test_end_to_end_forced_color_stripped():
    out = tool_bash({"command": r"printf '\x1b[31mRED\x1b[0m done\n'", "timeout": 8}, {"cwd": "/tmp"})
    assert "RED done" in out and "\x1b" not in out


def test_end_to_end_nul_dropped():
    out = tool_bash({"command": r"printf 'a\x00b\x00c'", "timeout": 8}, {"cwd": "/tmp"})
    assert "\x00" not in out and "abc" in out


def test_valid_unicode_survives():
    out = tool_bash({"command": r"printf '\xf0\x9f\x9a\xa2 ship\n'", "timeout": 8}, {"cwd": "/tmp"})
    assert "🚢" in out
