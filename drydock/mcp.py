"""MCP (Model Context Protocol) client — connect to MCP servers, expose their
tools to the agent.

Clean-room, stdlib only (no `mcp` SDK dependency): a minimal JSON-RPC 2.0 client
over the stdio transport (newline-delimited JSON), which is what local MCP
servers use. We do the initialize handshake, list the server's tools, and call
them on demand; results come back as text the model reads like any tool output.

Config (standard mcpServers shape, like Claude Desktop):
    ~/.drydock/mcp.json   and/or   <cwd>/.drydock/mcp.json
    {
      "mcpServers": {
        "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]},
        "weather": {"command": "python", "args": ["weather_server.py"], "env": {"KEY": "..."}}
      }
    }

Tools are registered as ``mcp__<server>__<tool>``. Everything degrades cleanly:
a server that fails to start or times out is skipped with a logged note, never
crashing drydock.

All logic original to Drydock.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

_PROTOCOL_VERSION = "2024-11-05"
# Live servers for this process, by name. The tool funcs look servers up here.
_SERVERS: dict[str, "MCPServer"] = {}


class MCPError(RuntimeError):
    pass


class MCPServer:
    """One stdio MCP server subprocess + a synchronous JSON-RPC channel."""

    def __init__(self, name: str, command: str, args: list[str], env: dict | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self._id = 0
        self._lock = threading.Lock()

    # ── transport ──────────────────────────────────────────────────────────
    def _send(self, msg: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _read_result(self, want_id: int, timeout: float = 20.0) -> dict:
        """Read newline-delimited JSON until the response with want_id arrives.
        Notifications / other-id messages are skipped. (Requests are serialized
        under _lock, so responses effectively arrive in order.)"""
        assert self.proc and self.proc.stdout
        import time
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                raise MCPError(f"{self.name}: timed out waiting for response {want_id}")
            line = self.proc.stdout.readline()
            if line == "":
                raise MCPError(f"{self.name}: server closed the connection")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # stray non-JSON (some servers log to stdout — ignore)
            if msg.get("id") == want_id:
                if "error" in msg:
                    raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})

    def _request(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            return self._read_result(rid, timeout=timeout)

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self, timeout: float = 20.0) -> None:
        env = {**os.environ, **self.env}
        self.proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, bufsize=1,
        )
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "drydock", "version": "1"},
        }, timeout=timeout)
        self._notify("notifications/initialized")
        result = self._request("tools/list", {}, timeout=timeout)
        self.tools = result.get("tools", []) or []

    def call(self, tool: str, arguments: dict, timeout: float = 60.0) -> str:
        result = self._request("tools/call", {"name": tool, "arguments": arguments or {}},
                               timeout=timeout)
        return _content_to_text(result)

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None


def _content_to_text(result: dict) -> str:
    """Flatten an MCP tools/call result (content blocks) to text."""
    if not isinstance(result, dict):
        return str(result)
    blocks = result.get("content")
    if isinstance(blocks, list):
        parts = []
        for b in blocks:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                else:
                    parts.append(json.dumps(b))
        text = "\n".join(p for p in parts if p)
    else:
        text = json.dumps(result)
    if result.get("isError"):
        text = f"[tool reported an error]\n{text}"
    return text or "(empty result)"


# ── config + connection ────────────────────────────────────────────────────

def config_paths(cwd: str) -> list[Path]:
    return [Path.home() / ".drydock" / "mcp.json", Path(cwd) / ".drydock" / "mcp.json"]


def load_config(cwd: str) -> dict:
    """Merge mcpServers from user + project config (project overrides by name)."""
    servers: dict[str, dict] = {}
    for p in config_paths(cwd):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        for name, spec in (data.get("mcpServers") or {}).items():
            if isinstance(spec, dict) and spec.get("command"):
                servers[name] = spec
    return servers


def connect_all(cwd: str, *, log=None) -> list[dict]:
    """Start every configured MCP server and return drydock tool schemas for
    their tools (namespaced mcp__<server>__<tool>). Failures are skipped."""
    _say = log or (lambda *_: None)
    schemas: list[dict] = []
    for name, spec in load_config(cwd).items():
        if name in _SERVERS:
            continue
        srv = MCPServer(name, spec["command"], spec.get("args") or [], spec.get("env") or {})
        try:
            srv.start()
        except (MCPError, OSError, Exception) as e:  # noqa: BLE001 — never crash startup
            _say(f"MCP server '{name}' failed to start: {e}")
            srv.stop()
            continue
        _SERVERS[name] = srv
        for t in srv.tools:
            schemas.append({
                "name": f"mcp__{name}__{t['name']}",
                "description": (t.get("description") or f"{name} tool {t['name']}")[:1024],
                "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
            })
        _say(f"MCP server '{name}': {len(srv.tools)} tool(s)")
    return schemas


def call(server: str, tool: str, arguments: dict) -> str:
    srv = _SERVERS.get(server)
    if srv is None:
        return f"MCP server '{server}' is not connected."
    try:
        return srv.call(tool, arguments)
    except MCPError as e:
        return f"MCP call failed: {e}"


def connected() -> dict[str, "MCPServer"]:
    return dict(_SERVERS)


def shutdown() -> None:
    for srv in _SERVERS.values():
        srv.stop()
    _SERVERS.clear()
