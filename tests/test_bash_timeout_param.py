"""The model-supplied `timeout` must be robust. Local models often send it as a
string ("10"); a 0/negative value made EVERY command time out instantly; an
absurd value could hang for hours. _coerce_timeout normalizes all of these."""
from __future__ import annotations


import pytest

from drydock.tools import _coerce_timeout, tool_bash


@pytest.mark.parametrize("raw,expected", [
    ("10", 10), (30, 30), (15.7, 15),      # numbers / stringified / float
    (0, 120), (-5, 120), ("abc", 120), (None, 120),  # invalid → default
    (99999, 1800), (100000, 1800),         # clamp to the 30-min ceiling
])
def test_coerce_timeout(raw, expected):
    assert _coerce_timeout(raw) == expected


def test_string_timeout_runs_instead_of_crashing():
    # previously raised TypeError in the '>' comparison
    out = tool_bash({"command": "echo hi", "timeout": "10"}, {"cwd": "/tmp"})
    assert "hi" in out


def test_zero_timeout_does_not_instant_timeout():
    out = tool_bash({"command": "echo hi", "timeout": 0}, {"cwd": "/tmp"})
    assert "hi" in out and "timed out" not in out
