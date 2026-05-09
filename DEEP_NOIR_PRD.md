# Deep Noir activation-patching PRD — reasoning steering, transformers-backed sidecar

**Status:** Draft, 2026-05-09. Phase 3 of HLE_PRD.

The v0 token-bias path (`LogitBiasSteeringApplier`) hit its expected ceiling: phase1_steered scored 1/20 = 5% on the worked-example seed, identical to the unsteered baseline. This PRD scopes the next-tier applier — real activation patching at a configurable decoder layer — using a `transformers`-backed sidecar process so we can validate the architecture before the bigger vLLM forward-hook port. Goal: get one steering vector to lift Phase 1 above 5/20 on the existing 20-question seed, proving reasoning steering works at all on quantized Gemma 4. Then port to vLLM for production speed.

## Why a sidecar (vs. patching the production server)

Production inference is llama.cpp Docker (remus, b9014). It does not expose a per-layer hidden-state hook, and rebuilding it with a custom hook costs weeks. vLLM has a `custom_ops` extension point but our prod stack moved off vLLM in May; rebuilding it for steering only would block other work.

A separate `transformers`-backed Python process gives us full forward-pass control today (`model.layers[L].register_forward_hook`), accepts the existing OpenAI-compatible request shape via a thin FastAPI wrapper, and runs on the same GPU pool. It will be **~10× slower than llama.cpp** because there's no batching/quant kernel, but research speed is the bottleneck right now, not throughput. Once one vector lifts Phase 1 we know the work is worth porting; until then, simple wins.

## Architecture

```
client (drydock TUI / hle_eval)
   │ chat/completions
   ▼
llm_balancer (:8001)
   │ routes by header X-Drydock-Steering
   ├─ no header  → llama.cpp :8000 (current prod, fast, no steering)
   └─ has header → drydock-steered :8002 (new sidecar, slow, hooks active)
```

- **`X-Drydock-Steering: show_work@18×0.6,verify@22×0.4`** — comma-separated `mode@layer×scale` triples. Empty/missing → balancer falls through to llama.cpp.
- **Sidecar process** (`drydock/steering/sidecar/server.py`):
  - Loads Gemma 4 26B via `transformers.AutoModelForCausalLM` with the AWQ-4bit weights from `/data3/Models/Gemma-4-26B-A4B-it-AWQ-4bit/`.
  - Pre-registers forward hooks on `model.layers[0..N]`. Each hook reads the requested mode/layer/scale from a thread-local context var and adds `scale × vector` to the residual stream.
  - Exposes `/v1/chat/completions` (OpenAI shape) so the existing balancer can route to it transparently.
- **`agent_loop.py` change**: when a `SteeringDecision` is active, set the request header `X-Drydock-Steering: <serialized>`. No payload change.

The wire-level result: one HTTP header is the entire integration surface between the harness and the steered inference path. Adding/removing modes is a config change, not a code change.

## Vector training

For each candidate mode, the data pipeline is:

1. **Extract contrastive pairs** from `~/.drydock/admiral_history.log` and stress run logs. A pair = `(prompt, derailed_chain, good_chain)` where derailed = stalled/looped/wrong-answer turn, good = same-prompt turn that resolved correctly. Tag schema lives in `drydock/admiral/schema.py` (already records turn-level outcome).
2. **Capture residual streams**. Run each pair through the unsteered sidecar with hooks recording `hidden_states[L]` at every layer for the assistant's first 50 tokens. Save as `.pt` per pair.
3. **Compute the direction**. For layer L, the vector is `mean(good_streams[L]) - mean(derailed_streams[L])`, L1-normalized. Stored as `.npy` + manifest `.toml` in `~/.drydock/steering/vectors/<mode>/<layer>.npy`.
4. **Validate via sandbox**. The existing `drydock/steering/sandbox.py` harness runs a fixed prompt set with steering on/off and diffs outputs. Pass = no regression on a control set + measurable behavior change on the target set.

Initial three modes to train (chosen because they have the most signal in admiral_history):

| Mode | Direction | Target layer | Hypothesis |
|---|---|---|---|
| `show_work` | derailed = single-line answers; good = step-by-step intermediate computations | 16-20 | Push the model to write intermediate steps before committing to a final answer |
| `verify` | derailed = wrong-answer-then-stop; good = wrong-answer-then-recheck-then-corrected | 18-22 | Push toward second-pass verification before emitting `ANSWER:` |
| `cite_source` | derailed = made-up specifics; good = "per the chunk above," explicit quote | 14-18 | When retrieval surfaces an authoritative chunk, push toward grounding rather than re-deriving |

`show_work` is the obvious first one to validate against Phase 1's 1/20 floor.

## Milestones

| # | Deliverable | Sized at |
|---|---|---|
| 1 | `drydock/steering/sidecar/server.py` — bare FastAPI + transformers loader on :8002, no hooks yet, returns identical outputs to llama.cpp on a smoke prompt. Container or systemd unit. | 1 day |
| 2 | Forward-hook injection scaffolding. Header parsing, context-var dispatch, vector load on demand. Hook is a no-op (adds zero) so we verify the integration end-to-end with no behavior change. | 1 day |
| 3 | Vector capture pipeline. Script that takes `(prompt, completion)` pairs, runs them through sidecar with capture-mode hooks, saves residual streams. | 1 day |
| 4 | Vector training. Compute direction per layer from captured streams, write `.npy` + `.toml`. Two pass: a quick "any non-zero direction works" pre-flight, then real training on real pairs. | 1 day |
| 5 | First end-to-end run: `show_work@18×0.5` against Phase 1's 20 questions. | 0.5 day (eval is 2-3h compute) |
| 6 | If lift > 0/20, train two more (`verify`, `cite_source`) and re-run. If lift = 0/20, iterate on layer choice and scale before adding modes. | open-ended |
| 7 | Optional follow-on: vLLM forward-hook port for production speed, once at least one vector lifts a real benchmark. | 1-2 weeks, only after #6 succeeds |

Total to first signal: ~4-5 days of focused work. The biggest single risk is #4 producing junk vectors because the contrastive pair set is too small or too noisy.

## Success criteria

- **Required:** end-to-end loop works. A request with `X-Drydock-Steering: show_work@18×0.5` returns a different completion than the same request with no header, on the same prompt, deterministic seed. Sandbox eval reports a structured diff. (This is the architecture validation — proves the wiring is real, independent of whether any vector helps.)
- **Aspirational:** `show_work` lifts Phase 1's 1/20 baseline by at least +1 question (≥10%). Confirms reasoning steering is a real lever for HLE-style tasks on a quantized 26B.
- **Stretch:** Two of the three trained modes lift Phase 1 ≥10% individually; combined ≥3/20. Suggests vector composition isn't catastrophic and the path scales.

## Constraints / non-goals

- **AWQ-4bit only.** No fp16/bf16 path. Vectors are calibrated against the production runtime — no drift between sandbox and (eventual) prod.
- **No vLLM port in this PRD.** Mentioned in milestone #7 as a follow-on; only triggers if the sidecar proves the architecture.
- **Sidecar runs on existing GPU pool.** Either remus or romulus, but only one at a time — competes with stress run for VRAM. Schedule eval runs around stress windows.
- **No "real-time learning."** Vectors are trained offline from logs and shipped as `.npy` files. The sidecar's only runtime job is the forward-pass injection; it does not adapt during a request.
- **No mainline drydock dependency on the sidecar.** Sidecar can be down; balancer falls back to llama.cpp; production unaffected.

## Decisions (locked 2026-05-09)

- **Pair source: admiral_history only for v1.** ~3 weeks of stress + autonomous_review traces is the contrastive set. No synthetic pair generation in this PRD. If volume turns out to be insufficient (vector training produces near-zero directions), revisit in a follow-up.
- **Layer sweep: informed picks first, full sweep as fallback.** Start with the ranges suggested in the modes table (`show_work` 16-20, `verify` 18-22, `cite_source` 14-18). Pick the midpoint of each range as the first attempt. If the informed pick fails to lift Phase 1 by ≥10%, escalate to a full sweep across all layers for that mode before declaring the mode dead.
- **Stress run flips to steering-on by default once one vector lifts Phase 1.** Confirmed-working vectors become the new baseline for stress; the unsteered run becomes the control measured in periodic A/B comparisons rather than the default. Set via env var `DRYDOCK_STEERING_MODES=<mode1>,<mode2>` in `stress_babysitter.sh`'s spawn line.

## File map (planned)

```
drydock/steering/
├── sidecar/
│   ├── server.py        # FastAPI + transformers, port :8002
│   ├── hooks.py         # forward_hook implementations
│   └── header_parser.py # X-Drydock-Steering parsing + validation
├── train/
│   ├── extract_pairs.py # admiral_history → contrastive .jsonl
│   ├── capture.py       # streams pairs through sidecar in capture mode
│   ├── compute_vector.py# good_mean - derailed_mean per layer
│   └── README.md        # training pipeline runbook
└── (existing applier.py, registry.py, vectors.py, sandbox.py — unchanged)
```

scripts/
├── start_steering_sidecar.sh    # systemd unit equivalent for the sidecar
└── train_steering_vector.sh     # one-shot wrapper around the train/ pipeline

## Reference signals (from current scaffolding)

- `drydock/steering/applier.py:14` — comment block already calls out `VllmSidecarSteeringApplier (TODO)` as the planned successor. This PRD picks up that thread and implements it as a transformers sidecar first.
- `drydock/core/agent_loop.py:2310` — the steering hook is already wired per-request and env-gated. Adding the header path is a 5-line change.
- `HLE_PRD.md` Phase 3 plan — names the three candidate modes (verify-before-answer, show-work-explicitly, consider-units, minimal-patch). This PRD adopts those as starting modes.
