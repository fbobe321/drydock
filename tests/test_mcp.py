"""MCP client: connect to a stdio MCP server, list + call its tools. Uses a real
(mock) server subprocess so the JSON-RPC handshake is exercised end to end."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from drydock import mcp

_SERVER = str(Path(__file__).parent / "fixtures" / "mock_mcp_server.py")


def _write_cfg(cwd: Path):
    d = cwd / ".drydock"
    d.mkdir(parents=True, exist_ok=True)
    (d / "mcp.json").write_text(json.dumps({
        "mcpServers": {"mock": {"command": sys.executable, "args": [_SERVER]}}
    }))


def teardown_function(_):
    mcp.shutdown()


def test_connect_lists_tools(tmp_path):
    _write_cfg(tmp_path)
    schemas = mcp.connect_all(str(tmp_path))
    names = [s["name"] for s in schemas]
    assert "mcp__mock__echo" in names
    assert mcp.connected()["mock"].tools[0]["name"] == "echo"


def test_call_tool_round_trips(tmp_path):
    _write_cfg(tmp_path)
    mcp.connect_all(str(tmp_path))
    out = mcp.call("mock", "echo", {"text": "hello mcp"})
    assert out == "echo: hello mcp"


def test_call_unknown_server_is_graceful(tmp_path):
    assert "not connected" in mcp.call("nope", "echo", {"text": "x"})


def test_missing_config_is_noop(tmp_path):
    assert mcp.connect_all(str(tmp_path)) == []


def test_bad_server_is_skipped(tmp_path):
    d = tmp_path / ".drydock"; d.mkdir(parents=True)
    (d / "mcp.json").write_text(json.dumps({
        "mcpServers": {"broken": {"command": "this_command_does_not_exist_xyz", "args": []}}
    }))
    logs = []
    schemas = mcp.connect_all(str(tmp_path), log=logs.append)
    assert schemas == []
    assert any("failed to start" in m for m in logs)
