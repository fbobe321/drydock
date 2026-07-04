# Using Graphify with Drydock (code knowledge graph over MCP)

[Graphify](https://github.com/safishamsi/graphify) (MIT) turns a folder of code,
docs, papers and images into a queryable **knowledge graph** — call graphs, the
most-connected "god nodes", shortest paths between concepts, PR impact, and more.
It exposes that graph as an **MCP stdio server**, and Drydock already speaks MCP,
so the agent can query your codebase's structure with no changes to Drydock.

This is complementary to Drydock's built-in [`/graphrag`](../README.md): `/graphrag`
is a zero-dependency, fully-local KB; Graphify adds richer multi-language
code-structure analysis (tree-sitter ASTs, call graphs, community detection).

Verified end-to-end through the real Drydock TUI (Graphify 0.9.5): the agent
autonomously called `mcp__graphify__god_nodes` and answered from the graph.

## 1. Install Graphify

```bash
pip install "graphify[mcp]"      # the [mcp] extra pulls the MCP SDK
```

## 2. Build a graph from your project

**Structural only (offline, no LLM) — fast, fully private:**

```bash
cd /path/to/your/project
graphify . --no-cluster --no-label      # tree-sitter AST extraction → graphify-out/graph.json
```

**Full semantic (concepts from prose, community labels) — uses an LLM.**
Graphify supports any OpenAI-compatible endpoint, so point it at your **local**
model server (the same one Drydock uses) and nothing leaves the machine:

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1   # your llama.cpp / vLLM server
export OPENAI_MODEL=gemma4
export OPENAI_API_KEY=sk-noauth                    # llama.cpp ignores it; must be set
graphify .
```

`graphify update <path>` re-extracts code with **no LLM** for incremental rebuilds.

## 3. Point Drydock at it

Add a `graphify` entry to `~/.drydock/mcp.json` (global) **or**
`<project>/.drydock/mcp.json` (per-project). Use absolute paths:

```json
{
  "mcpServers": {
    "graphify": {
      "command": "graphify-mcp",
      "args": [
        "--transport", "stdio",
        "--graph", "/abs/path/to/your/project/graphify-out/graph.json"
      ]
    }
  }
}
```

A copy-paste starting point ships at [`examples/mcp/graphify.json`](../examples/mcp/graphify.json).

> If `graphify-mcp` isn't on Drydock's `PATH` (e.g. you installed it into a venv),
> use the absolute path to the executable as `command`, e.g.
> `/path/to/venv/bin/graphify-mcp`.

## 4. Use it

Launch Drydock in that project and run `/mcp` — you should see:

```
MCP servers (1 connected):
  • graphify: query_graph, get_node, get_neighbors, get_community, god_nodes,
    graph_stats, shortest_path, list_prs, get_pr_impact, triage_prs
```

The agent calls these automatically (as `mcp__graphify__<tool>`) — just ask it
things like *"use the graph to find the most connected modules"* or *"what's the
shortest path between the auth layer and the database code?"*. You never type the
tool names yourself.

## Notes

- **Keep the graph fresh**: re-run `graphify update .` (no LLM) after code changes,
  or `graphify --watch .` to auto-rebuild.
- **Fully local is the default here** — structural builds need no network, and the
  semantic build uses your local model via `OPENAI_BASE_URL`. Drydock never sends
  your code anywhere; Graphify's `SECURITY.md` states it makes no network calls
  during analysis.
- **Restart Drydock** after editing `mcp.json` (servers connect at startup).
