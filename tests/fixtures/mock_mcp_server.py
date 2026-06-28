#!/usr/bin/env python3
"""A minimal stdio MCP server for tests: speaks JSON-RPC 2.0 over newline-
delimited JSON. Exposes one tool, `echo`, that returns its `text` argument."""
import json
import sys

TOOLS = [{
    "name": "echo",
    "description": "Echo back the given text.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}]


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, rid = msg.get("method"), msg.get("id")
        if method == "initialize":
            res = {"protocolVersion": "2024-11-05", "capabilities": {},
                   "serverInfo": {"name": "mock", "version": "1"}}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            args = msg.get("params", {}).get("arguments", {})
            res = {"content": [{"type": "text", "text": f"echo: {args.get('text', '')}"}]}
        elif rid is None:
            continue  # a notification (e.g. notifications/initialized)
        else:
            res = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
