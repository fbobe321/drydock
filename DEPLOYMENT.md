# DryDock Deployment Guide

The known-working configuration DryDock is developed and tested against.
If you hit TUI loops, tool errors, or model hangs that look different
from what the README describes, your setup is probably drifting from
this baseline. This document is kept deliberately concrete — it mirrors
a real working machine, not aspirational recommendations.

## Hardware baseline

| Component | What works |
|---|---|
| GPU | 2× NVIDIA RTX 4060 Ti 16 GB (32 GB total VRAM) |
| Model | [Gemma-4-26B-A4B-it-AWQ-4bit](https://huggingface.co/casperhansen/gemma-4-26b-a4b-it-AWQ-4bit) |
| RAM | 64 GB recommended (32 GB minimum) |
| Disk | SSD, ≥100 GB free for model + session logs |
| OS | Ubuntu 22.04, kernel 6.8 |

A single 24 GB GPU can run the model with slightly reduced context.
The AWQ quant is what makes 16 GB cards viable — non-quantized
Gemma 4 26B needs ≥40 GB.

## vLLM (model server)

Run via Docker:

```bash
docker run -d \
    --gpus all \
    --name gemma4 \
    -p 8000:8000 \
    -v /path/to/models:/models \
    --ipc=host \
    vllm/vllm-openai:gemma4 \
    --model /models/Gemma-4-26B-A4B-it-AWQ-4bit \
    --quantization compressed-tensors \
    --tensor-parallel-size 2 \
    --max-model-len 131072 \
    --max-num-seqs 2 \
    --gpu-memory-utilization 0.95 \
    --kv-cache-dtype fp8 \
    --served-model-name gemma4 \
    --trust-remote-code \
    --tool-call-parser gemma4 \
    --enable-auto-tool-choice \
    --attention-backend TRITON_ATTN
```

**Don't omit** any of these flags — they're all load-bearing:

| Flag | Why |
|---|---|
| `--tool-call-parser gemma4` | Without this, Gemma 4 emits tool calls in text that the OpenAI-compat layer can't route. Tool calling silently breaks. |
| `--enable-auto-tool-choice` | Required so `tool_choice="auto"` actually works. Without it the model can't decide between text and tool call. |
| `--attention-backend TRITON_ATTN` | The default backend has periodic OOM spikes on 16 GB cards. TRITON is stable. |
| `--kv-cache-dtype fp8` | Halves KV cache memory; required to reach 131K context on 32 GB total VRAM. |
| `--max-num-seqs 2` | Prevents OOM under concurrent load. Raise on larger cards. |
| `--gpu-memory-utilization 0.95` | Squeezes the last bit of VRAM. Drop to 0.85 if you see allocator warnings. |

Single-GPU variant: drop `--tensor-parallel-size 2` and lower
`--max-model-len` to something like 65536.

### Sanity-check

```bash
curl http://localhost:8000/v1/models
# Should return: {"data": [{"id": "gemma4", ...}]}

curl http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gemma4","messages":[{"role":"user","content":"hi"}]}'
```

## Optional: load balancer / failover

For long sessions, a thin HTTP proxy in front of vLLM survives brief
vLLM restarts. Point DryDock at the proxy, not vLLM directly. See
`scripts/llm_balancer.py` for reference; typical setup is:

- Proxy on port 8001 → vLLM on port 8000
- Keepalive cron respawns the balancer if it dies
- `scripts/vllm_failover.sh` on a 5-min cron restarts vLLM if it's unreachable

The development machine uses this setup. Skip it for first-time setup;
return to it when you start hitting multi-hour sessions.

## `~/.drydock/config.toml`

The known-working DryDock config. Copy into `~/.drydock/config.toml`
after first run (DryDock creates a stub on first launch; overwrite it):

```toml
active_model = "local"
auto_compact_threshold = 120000
api_timeout = 600.0
system_prompt_id = "ralph"           # Gemma 4's simplified prompt
slim_system_prompt = true
enable_telemetry = true
enable_notifications = true

[[providers]]
name = "vllm"
api_base = "http://localhost:8000/v1"   # or :8001 if using llm_balancer
api_style = "openai"
backend = "generic"
reasoning_field_name = "reasoning_content"

[[models]]
name = "gemma4"
provider = "vllm"
alias = "local"                       # matches active_model above
temperature = 0.2
thinking = "high"

[session_logging]
save_dir = "~/.vibe/logs/session"     # drydock writes sessions here
session_prefix = "session"
enabled = true

[project_context]
max_chars = 40000
max_doc_bytes = 32768
max_files = 1000
max_depth = 3
timeout_seconds = 2.0

[tools.bash]
permission = "always"
max_output_bytes = 16000
default_timeout = 300
# See repo config for the full allowlist/denylist.

[tools.read_file]
permission = "always"
max_read_bytes = 64000

[tools.write_file]
permission = "always"
max_write_bytes = 64000
create_parent_dirs = true

[tools.search_replace]
permission = "always"
max_content_size = 100000
fuzzy_threshold = 0.9

[tools.grep]
permission = "always"
max_output_bytes = 64000
default_max_matches = 100
default_timeout = 60

[tools.todo]
permission = "always"
max_todos = 100

[tools.task]
permission = "always"
allowlist = ["explore", "diagnostic", "planner", "builder"]

[tools.web_fetch]
permission = "ask"         # don't auto-approve network egress
default_timeout = 30
max_content_bytes = 512000

[tools.web_search]
permission = "ask"
```

Critical fields:

- `temperature = 0.2` — Gemma 4 gets unstable above ~0.4. Don't raise.
- `thinking = "high"` — required for planning. DryDock adaptively
  lowers it per-turn; setting it here is the default ceiling.
- `system_prompt_id = "ralph"` — the Gemma-4-tuned prompt. The default
  cli prompt confuses Gemma 4 into planning instead of acting.
- `auto_compact_threshold = 120000` — below Gemma 4's 131K max context,
  with headroom for the response.
- `api_timeout = 600.0` — Gemma 4's first-turn thinking can legitimately
  take 60–120 s. The default timeout is too short.

## Environment variables

| Var | Default | Why you'd change it |
|---|---|---|
| `DRYDOCK_AUTO_CONTINUE_DISABLE` | unset | Set to `1` to stop the auto-"Continue." injection that keeps Gemma 4 chaining tool calls. Useful for **stress test runs** and any workflow where text-only turns should cleanly end the user turn. Leave unset for production use unless you see Continue-loops. |
| `DRYDOCK_INSECURE` | unset | Set to `1` to accept self-signed TLS (local LAN deployments). |
| `DRYDOCK_LOCAL_URL` | `http://localhost:8000/v1` | Override the default local endpoint. |
| `DRYDOCK_LOCAL_MODEL` | `local` | Model alias to target. |

## Known-good network topology

```
user        drydock TUI           llm_balancer         vLLM
cli   ───►  :stdin           ───►  :8001      ────►  :8000
                                  │                    │
                                  └── keepalive cron   └── keeps one
                                       (5-min)              container up
```

## Diagnosing bad installs

If DryDock acts up, compare your runtime to this baseline before
filing an issue:

1. **`curl localhost:8000/v1/models`** returns Gemma 4? If not, vLLM
   isn't running or isn't on the expected port.
2. **`python3 -c "from drydock.core.agent_loop import AgentLoop; import inspect; print('aafa090' in inspect.getsource(AgentLoop._sanitize_message_ordering))"`** — checks the should_break_loop fix is in your install. If `False`, reinstall with `pip install --upgrade drydock-cli`.
3. **Check the session log.** `tail ~/.vibe/logs/session/session_*/messages.jsonl` — look for `"user": "Continue."` repeating. If you see that, set `DRYDOCK_AUTO_CONTINUE_DISABLE=1`.
4. **`docker logs gemma4 | tail -50`** — vLLM errors show up here. Common: `out of memory` (drop `--max-num-seqs` or `--max-model-len`), `unknown tool-call parser` (you're on an older vLLM image without the `gemma4` parser).

## Tested versions

Everything in this document was exercised on:

- drydock-cli ≥ 2.6.145
- vllm/vllm-openai:gemma4 Docker image (built from vLLM 0.7.x + Gemma 4 parser patch)
- Python 3.12 (dev) / 3.14 (runtime)
- Ubuntu 22.04, kernel 6.8, Docker 24.x with nvidia-container-toolkit
- NVIDIA driver 550.x

Older vLLM images may lack `--tool-call-parser gemma4`. Build from the
gemma4 branch or wait for upstream.
