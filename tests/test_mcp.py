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


def test_content_to_text_joins_text_blocks():
    out = mcp._content_to_text({"content": [{"type": "text", "text": "line1"},
                                            {"type": "text", "text": "line2"}]})
    assert out == "line1\nline2"


def test_content_to_text_non_text_block_is_json():
    out = mcp._content_to_text({"content": [{"type": "image", "data": "abc"}]})
    assert '"type": "image"' in out and '"data": "abc"' in out


def test_content_to_text_error_and_empty_and_nondict():
    assert mcp._content_to_text({"content": [{"type": "text", "text": "boom"}],
                                 "isError": True}).startswith("[tool reported an error]")
    assert mcp._content_to_text({"content": []}) == "(empty result)"
    assert mcp._content_to_text("raw string") == "raw string"


def test_load_config_project_overrides_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    user = tmp_path / ".drydock"; user.mkdir()
    (user / "mcp.json").write_text('{"mcpServers": {"db": {"command": "user-cmd"}}}')
    proj = tmp_path / "proj" / ".drydock"; proj.mkdir(parents=True)
    (proj / "mcp.json").write_text('{"mcpServers": {"db": {"command": "proj-cmd"},'
                                   ' "nocmd": {"args": []}}}')
    cfg = mcp.load_config(str(tmp_path / "proj"))
    assert cfg["db"]["command"] == "proj-cmd"   # project wins
    assert "nocmd" not in cfg                    # missing command → skipped
