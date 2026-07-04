# ⚓ Drydock

A local-first, provider-agnostic **terminal coding agent** for your own LLM.
No accounts, no telemetry, no cloud — the only outbound calls are to the model
endpoint you configure and (optionally) the web-search tools you invoke.
Primary target: **dense Gemma-4-31B** (QAT, 64K) served by llama.cpp on a
single workstation.

> **v3 — clean-room rebuild.** Drydock is being rebuilt as an original,
> Apache-2.0 codebase owned end to end (no upstream fork). Every release is
> gated by a credential-exfiltration scanner that blocks anything reaching
> off-box. See [`HARNESS_DESIGN.md`](HARNESS_DESIGN.md) and
> [`docs/PRD.md`](docs/PRD.md).

## Why

A coding agent should build real projects from your machine without sending
your code or credentials anywhere. Drydock runs entirely against a local
model, feels like a first-class terminal agent, and keeps its data plane on
your box.

## Status

Shipping. Published on PyPI as **`drydock-cli`** (v3.x). The Textual TUI is the
default surface: a scrolling transcript with streamed assistant text, collapsible
tool cards, collapsible reasoning ("thinking") cards, a live nautical activity
line, and a multi-line prompt. The agent loop, OpenAI-compatible provider,
two-tier compaction, and the full agentic toolset (below) are in, with Gemma
reliability hardening verified hands-on.

## Capabilities

A full agentic CLI harness — every tool below is clean-room and dependency-free
(nothing beyond `openai` + `textual`), and the model calls them autonomously:

- **Files & shell** — `Read` (with a structure index for huge files), `Write`,
  `Edit`, `Bash`, `Glob`, `Grep`.
- **Vision (multimodal)** — reference an image path in your message (a `.png`/
  `.jpg` screenshot, mockup, or diagram) and it's attached for a vision-capable
  model to *see*; the agent can also call the `ViewImage` tool to look at an
  image it discovers on its own — describe a UI, read text off a screenshot,
  debug a diagram. Works with any `--mmproj` server (e.g. llama.cpp + a vision model).
- **Version control** — `GitStatus`, `GitDiff`, `GitLog`, `GitCommit`
  (structured + truncated; commit is local and reversible).
- **Internet** — `WebSearch` + `WebFetch` (DuckDuckGo; offline-safe).
- **Knowledge base (GraphRAG)** — build a local entity-graph index from your
  docs/code with `/graphrag build <path>`; the agent retrieves from it via the
  read-only `Knowledge` tool.
- **Multi-agent** — `Dispatch` runs several read-only sub-agents in parallel;
  `task` runs one — each in a fresh context, for focused investigation.
- **Second-model advisor** — point `/advisor` at a stronger model on any
  OpenAI-compatible endpoint (e.g. Gemini, or a proxy on another box); the agent
  calls the `Consult` tool for a second opinion when stuck, and you can `/ask
  <question>` directly. Opt-in, user-configured — no extra dependency.
- **MCP** — connect to Model Context Protocol servers (`~/.drydock/mcp.json`);
  their tools appear as `mcp__<server>__<tool>`. List them with `/mcp`. Works with
  third-party servers out of the box — e.g. [Graphify](https://github.com/safishamsi/graphify)
  for a queryable code knowledge graph: see [docs/graphify.md](docs/graphify.md)
  and the copy-paste [example config](examples/mcp/graphify.json).
- **Skills** — reusable `/<name>` commands authored as markdown in
  `~/.drydock/skills/` (or `<project>/.drydock/skills/`); `$ARGS` substitution.
- **Loops** — `/loop <count> <prompt>` runs a prompt iteratively (Esc stops).

## Slash commands

Typed into the prompt. The agent also knows these, so you can just **ask it**
("how do I add my own docs?") and it'll point you to the right one.

| Command | What it does |
| --- | --- |
| `/graphrag build <path>` | Build a knowledge base from a file or folder of docs/code |
| `/graphrag add <path>` | Incrementally add more documents to the base |
| `/graphrag query <q>` | Test what the base returns (no model) |
| `/graphrag status` · `clear` | List indexed sources · wipe the base |
| `/skills` | List your skills |
| `/skills new <name> <prompt>` | Create a reusable `/<name>` skill (use `$ARGS` for input) |
| `/<name>` | Run a skill |
| `/loop <count> <prompt>` | Repeat a prompt N times (Esc stops) |
| `/mcp` | List connected MCP servers + their tools |
| `/rmf bootstrap [families]` | Ingest the NIST SP 800-53 catalog (RMF automation) |
| `/rmf-control` · `/rmf-categorize` · `/rmf-review` · `/rmf-poam` | Bundled RMF skills |
| `/stig new <xccdf>` | Generate a blank `.ckl` from a DISA STIG benchmark |
| `/stig <ckl>` · `/stig <ckl> open` | Summarize a checklist · list findings by status |
| `/stig graph <ckl>` | Ingest a checklist into the RMF graph (auto-links rules→controls via CCI) |
| `/stig-assess <ckl>` · `/stig-remediate <ckl> <rule>` | Assess a rule vs evidence · write a fix script |
| `/model` · `/cwd` | Show/set model & endpoint · working directory |
| `/undo` · `/back` | Revert the last write · rewind the last turn |
| `/compact` · `/context [n]` | Shrink context now · view/set the context-window budget |
| `/advisor` · `/ask <q>` | Set up a 2nd 'advisor' model (Gemini etc.) · consult it |
| `/status` · `/clear` · `/help` · `/quit` | Session stats · reset · help · exit |

### Knowledge base (GraphRAG) — ingesting your documents

```
/graphrag build ./docs        # index a file or a whole folder
/graphrag add ./more_docs     # add more later, incrementally
/graphrag query "how are refunds handled?"   # check retrieval
/graphrag status              # what's indexed
```

Once built, the agent **automatically** retrieves from it (read-only `Knowledge`
tool) when a question touches your material. Ingests text formats
(`.md .txt .py .js .json .yaml .sql …`), **PDF and Word (`.docx`)**, and **STIG
checklists (`.ckl`/`.cklb`)** — checklists are flattened to per-rule findings so
you can ask "which findings are open?". `.docx`/`.ckl` need nothing extra; PDF
uses the `pdftotext` binary (poppler) if present, else `pip install
drydock-cli[pdf]` (pypdf). The index is a single JSON at
`<project>/.drydock/graphrag.json` — clean-room, no embeddings.

### Custom skills

```
/skills new commitmsg  Write a concise conventional-commit message for: $ARGS
/commitmsg the staged auth changes      # runs the skill with $ARGS substituted
```

Skills are markdown files in `~/.drydock/skills/` (personal) or
`<project>/.drydock/skills/` (project); `/skills new` writes one for you.

### Second-model advisor (a stronger model for a second opinion)

Drydock's primary model is a small local one. You can wire in a **second,
stronger model** — e.g. **Gemini** — to consult when the local model is stuck or
you want to sanity-check a design. It's just another OpenAI-compatible endpoint,
so there's **no extra dependency**; it's **opt-in** and off until you configure
it (the only call is the one you point it at — consistent with the no-phone-home
stance).

**Configure it** (persists to `~/.drydock/config.toml`):
```
/advisor url    http://<other-box>:4000/v1     # any OpenAI-compatible endpoint
/advisor model  gemini-2.5-pro
/advisor key    <api-key>                       # if the endpoint needs one
/advisor test                                   # ping it: reachable? which model? latency?
/advisor                                         # show current config (key masked)
```

**Use it** three ways:
- **You (private):** `/ask <question>` — consults the advisor and shows its
  answer to *you only* (not added to the agent's context).
- **You (feed the agent):** `/ask! <question>` — same, but also **injects** the
  answer into the agent's context and has the primary model process it, so a
  second opinion can steer the current task.
- **The agent:** it can call the read-only `Consult` tool on its own when it hits
  something hard (the answer comes back as a tool result, so it's in context).

**Pointing it at Gemini** — two options:
1. **Gemini's official OpenAI-compatible endpoint** (if this box has internet):
   ```
   /advisor url   https://generativelanguage.googleapis.com/v1beta/openai
   /advisor model gemini-2.5-pro
   /advisor key   <your-gemini-api-key>
   ```
2. **A proxy on another box** (e.g. where your key lives). LiteLLM is one line:
   ```bash
   pip install 'litellm[proxy]'; export GEMINI_API_KEY=...
   litellm --model gemini/gemini-2.5-pro --host 0.0.0.0 --port 4000
   ```
   then `/advisor url http://<that-box-ip>:4000/v1`.

Any OpenAI-compatible model works here (another local server, a hosted model,
etc.) — Gemini is just the common case.

### RMF automation (NIST SP 800-53)

For Risk Management Framework work, Drydock can ingest the NIST SP 800-53 Rev 5
control catalog into the knowledge base and ships four RMF skills — all
**100% local** for CUI/sensitive systems.

```
/rmf bootstrap            # one-time: fetch + ingest the 800-53 catalog (offline after)
/graphrag build ./ssp     # ingest your own SSP/POA&M (PDF/Word/text)
/rmf-control AC-2         # look up a control
/rmf-categorize ...       # FIPS 199 categorization + tailored baseline
/rmf-review AC-2          # review an SSP implementation statement vs 800-53A
/rmf-poam <finding>       # generate a POA&M entry from a scan/STIG finding
```

Beyond text retrieval, `/rmf bootstrap` also builds a **typed ontology graph**
(Control / Component / Vulnerability nodes; IMPLEMENTS / RESIDES_ON / ASSESSES
edges). The agent records your system topology with `GraphAdd` and traces
relationships with `GraphQuery` — including **control inheritance** ("which
servers inherit physical controls from their enclave?"). Stdlib in-memory graph,
no Neo4j.

### STIG checklists (DISA `.ckl`/`.cklb`)

Take a raw DISA STIG benchmark all the way to a completed, eMASS/STIG-Viewer-
compatible checklist — entirely local (hostnames, IPs, and findings are CUI):

```
/stig new U_ASD_STIG_V6R1_Manual-xccdf.xml app.ckl   # benchmark → blank .ckl
/graphrag build ./app                                 # pull in the app's evidence
/loop 286 /stig-assess app.ckl                        # assess each rule vs evidence
/stig app.ckl open                                    # list the open findings
/stig-remediate app.ckl SV-900010r1_rule              # write an idempotent fix script
/stig graph app.ckl                                   # ingest + auto-link rules → NIST controls
/stig poam app.ckl                                    # eMASS POA&M CSV of the open findings
```

`/stig new` parses the XCCDF benchmark (validated against the full 286-rule
Application STIG); `/stig-assess` reads your evidence and writes status +
finding-details back in place; `/stig graph` builds `STIG`/`STIG-Rule` nodes and
**auto-links each rule to its NIST 800-53 control** through DISA's CCI map
(`Control —SATISFIED_BY→ rule`), fetched once and cached offline. `/stig poam`
exports the open findings to a deterministic **eMASS POA&M CSV** — Control (from
the CCI map), Vulnerability Description, `POA&M Status=Ongoing`, Milestone (the
Fix Text), and Severity (CAT I/II/III → High/Moderate/Low) — no LLM, stdlib only.

## Install

```bash
pip install drydock-cli
drydock
```

Requires Python 3.11+. From source instead:
`git clone https://github.com/fbobe321/drydock-v3.git && cd drydock-v3 && pip install -e .`

On first launch with no config, Drydock probes localhost for a running local
LLM (llama.cpp/vLLM `:8000`, Ollama `:11434`, LM Studio `:1234`) and wires up
the first one it finds — no account or API-key prompt. Override anytime with
`--model` / `--provider` / `--base-url` or `~/.drydock/config.toml`.

## Using it

Type a task and press **Enter**. Drydock reads/writes/edits files and runs
commands to do the work, showing each as a collapsible tool card.

- **Enter** submits · **Ctrl+J** newline (multi-line prompts)
- **↑ / ↓** recall command history (persists across sessions)
- **PgUp / PgDn** (and **Ctrl+Home/End**) scroll the transcript
- **Ctrl+O** expand/collapse tool output · **drag + Ctrl+C** copy a selection
- **Ctrl+C twice** (or **Ctrl+D**, `/quit`) to exit
- A live activity line shows progress while it works:
  `◡ Keelhauling…  (12s · ↓ 6.2k tokens · thinking with high effort)`
- Submit while it's working and the prompt **queues** (drains in order)
- Slash commands: `/model` · `/cwd` · `/undo` (revert last write) · `/back`
  (rewind last turn) · `/status` · `/compact` (shrink context) · `/context`
  (view/set the context-window budget) · `/graphrag` (build/query a knowledge
  base) · `/skills` (list your `/<name>` skills) · `/loop` (repeat a prompt) ·
  `/mcp` (list MCP servers) · `/rmf` & `/stig` (NIST 800-53 / DISA STIG
  automation) · `/clear` · `/help` · `/quit`

It honors `AGENTS.md` / `DRYDOCK.md` in the working directory for project
conventions.

## Safety

Two tiers, plus advisory guards — all designed so legitimate work is never
blocked:

- **Catastrophic denylist** — commands like `rm -rf /`, `mkfs`, raw block-device
  writes, and fork bombs are refused outright (never run).
- **Approval prompt** — sensitive-but-legitimate commands (`sudo`, package
  installs, network fetches, `git push`) pause for **Allow / Always / Deny**.
- **Advisory write guards** — Drydock flags (never blocks) Python syntax errors,
  stub-only files, imports of sibling modules that don't exist yet, bare
  `raise` outside an except, and refuses to write git conflict-marker content.

Point it at a local OpenAI-compatible endpoint (e.g. llama.cpp's `server-cuda`
serving Gemma-4-31B). The web tools (`WebSearch`/`WebFetch`) are read-only and
degrade cleanly offline; the release scanner allowlists only the search backend.

## Model server (reference setup)

Drydock is provider-agnostic, but it's tuned and measured against this rig:

- **Model:** dense **Gemma-4-31B** (QAT `Q4_K_XL` GGUF), served by
  `ghcr.io/ggml-org/llama.cpp:server-cuda` with `--jinja`. Swapped from the
  26B-A4B MoE, whose ~4B active params caused fatal agentic tool-loops; the
  dense 31B is loop-free (slower, but it finishes).
- **GPUs:** 2× NVIDIA RTX 4060 Ti 16GB, **tensor-split** across both cards
  (`--tensor-split 1,1`) so the 31B weights fit.
- **Context:** 64k (`-c 65536`) with `q8_0` KV-cache quantization
  (`-ctk q8_0 -ctv q8_0`); set `context_limit` in `~/.drydock/config.toml` to
  match your server's `-c`.
- **Throughput:** ~15 tok/s decode (tensor-split 31B). Faster single-GPU
  options exist if you drop to a smaller model.
- **Provider-agnostic:** any OpenAI-compatible endpoint (llama.cpp, vLLM,
  Ollama, LM Studio) works — point `--base-url` at it.

## Principles

- **Clean provenance** — original code only; nothing copied from any other
  project.
- **Local-only data plane** — no telemetry, no phone-home, no hardcoded
  third-party hosts, no credential transmission.
- **Advisory, never blocking** — loop/safety mechanisms inject better
  context; they never hard-stop legitimate work.
- **The scanner is law** — `scripts/security_scan.py` gates every release.

## Security scan

```bash
python3 scripts/security_scan.py drydock/      # scan the source tree
python3 scripts/security_scan.py dist/*.whl    # scan a built wheel
```

Exit 2 (HIGH finding) blocks a release.

## License

Apache-2.0, © 2026 Frank Bobe III. See [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE).
