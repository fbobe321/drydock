"""Tests for canonical tool namespacing (PRD Epic D): builtin + MCP share one
naming scheme for routing/policy without changing model-facing names."""
from __future__ import annotations

from drydock.tool_registry import canonical_name, has_namespace_collision


def test_builtin_canonical_name():
    # PRD D1.1: builtin.read
    assert canonical_name("Read") == "builtin.read"
    assert canonical_name("Bash") == "builtin.bash"


def test_mcp_canonical_name():
    # PRD D1.2: github.create_issue
    assert canonical_name("mcp__github__create_issue") == "github.create_issue"


def test_two_servers_same_tool_do_not_collide():
    # PRD D1.3: github.search and documentation.search are distinct.
    names = ["mcp__github__search", "mcp__documentation__search"]
    assert canonical_name(names[0]) == "github.search"
    assert canonical_name(names[1]) == "documentation.search"
    assert not has_namespace_collision(names)


def test_builtin_and_mcp_mixed_no_collision():
    names = ["Read", "Write", "mcp__github__search", "mcp__docs__search"]
    assert not has_namespace_collision(names)


def test_collision_detected_for_true_duplicates():
    assert has_namespace_collision(["Read", "read"])  # both -> builtin.read


def test_mcp_server_only_fallback():
    assert canonical_name("mcp__weird") == "mcp.weird"
