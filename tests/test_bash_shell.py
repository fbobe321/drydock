"""tool_bash runs commands under bash (executable=_BASH_SHELL) so the bash syntax
the model naturally writes works — on Debian/Ubuntu /bin/sh is dash, which rejects
[[ ]], <<<, arrays, {1..n}, and process substitution with confusing syntax errors
the model then loops on. Found while investigating the /bin/sh limitation."""
from __future__ import annotations

import pytest

from drydock.tools import tool_bash, _BASH_SHELL, _detect_bash


def _run(cmd):
    return tool_bash({"command": cmd, "timeout": 6}, {"cwd": "/tmp"}).strip()


def test_bash_is_detected():
    assert _BASH_SHELL and _BASH_SHELL.endswith("bash")


@pytest.mark.skipif(not _BASH_SHELL, reason="bash not installed")
@pytest.mark.parametrize("cmd,expected", [
    ('cat <<< "hello world"', "hello world"),
    ('[[ 5 -gt 3 ]] && echo yes', "yes"),
    ('echo {1..5}', "1 2 3 4 5"),
    ('a=(x y z); echo ${a[1]}', "y"),
    ('diff <(echo a) <(echo a) && echo same', "same"),
])
def test_bashisms_work(cmd, expected):
    assert expected in _run(cmd)


def test_plain_commands_and_exit_codes_unaffected():
    assert "hello" in _run("echo hello")
    assert "[exit code: 1]" in _run("false")
    assert "hi" in _run("echo hi | cat")


def test_detect_bash_returns_path_or_none():
    r = _detect_bash()
    assert r is None or (isinstance(r, str) and "bash" in r)
