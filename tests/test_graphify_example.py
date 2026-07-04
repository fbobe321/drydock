"""The bundled Graphify MCP example (docs/graphify.md + examples/mcp/graphify.json)
must stay valid + in the shape drydock's MCP loader expects, so the copy-paste
recipe never rots."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE = _ROOT / "examples" / "mcp" / "graphify.json"
_DOC = _ROOT / "docs" / "graphify.md"


def test_example_is_valid_json_with_mcpservers_shape():
    cfg = json.loads(_EXAMPLE.read_text())
    assert "mcpServers" in cfg and "graphify" in cfg["mcpServers"]
    srv = cfg["mcpServers"]["graphify"]
    assert srv["command"] == "graphify-mcp"
    assert "--transport" in srv["args"] and "stdio" in srv["args"]
    # the same shape drydock/mcp.py consumes: command + args list
    assert isinstance(srv["args"], list)


def test_example_matches_drydock_mcp_loader_shape():
    """Instantiating an MCPServer from the example must not raise (parses the
    command/args the way connect_all does), without starting it."""
    from drydock.mcp import MCPServer
    cfg = json.loads(_EXAMPLE.read_text())["mcpServers"]["graphify"]
    srv = MCPServer("graphify", cfg["command"], cfg["args"], cfg.get("env"))
    assert srv.command == "graphify-mcp" and srv.args[0] == "--transport"


def test_doc_exists_and_links_example():
    text = _DOC.read_text()
    assert "graphify-mcp" in text and "OPENAI_BASE_URL" in text
    assert "examples/mcp/graphify.json" in text
