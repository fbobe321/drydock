"""tool_bash must NOT inherit the caller's stdin (in the TUI that's Textual's
terminal): a command that reads stdin should get immediate EOF, not hang waiting
for input or steal the user's keystrokes. The agent still feeds input explicitly
via a pipe/redirect, which overrides the DEVNULL stdin."""
from __future__ import annotations

import time

from drydock.tools import tool_bash


def _run(cmd, timeout=6):
    t0 = time.monotonic()
    out = tool_bash({"command": cmd, "timeout": timeout}, {"cwd": "/tmp"})
    return time.monotonic() - t0, out


def test_stdin_reader_gets_eof_not_hang():
    dt, out = _run("read x; echo \"got:[$x]\"")
    assert dt < 2.0 and "got:[]" in out, "reading stdin should EOF instantly, not hang"


def test_cat_no_input_eofs():
    dt, out = _run("cat && echo DONE")
    assert dt < 2.0 and "DONE" in out


def test_python_input_raises_eoferror_cleanly():
    _, out = _run('python3 -c "input()" 2>&1 | tail -1')
    assert "EOFError" in out


def test_piped_input_still_works():
    _, out = _run("echo hello | cat")
    assert "hello" in out


def test_redirect_from_file_still_works():
    _, out = _run("printf 'a\\nb\\n' > /tmp/ddin.txt; wc -l < /tmp/ddin.txt")
    assert "2" in out
