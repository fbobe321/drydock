"""Deep Noir activation-steering sidecar — transformers-backed inference.

A separate Python process (default port 8002) that loads Gemma 4 via
`transformers.AutoModelForCausalLM` so we have full forward-pass control
for activation patching at configurable decoder layers. Speaks the
OpenAI `/v1/chat/completions` shape so the existing `llm_balancer`
can route to it transparently when an `X-Drydock-Steering` header is
present on the request.

Why a sidecar (not a vLLM patch): vLLM doesn't expose per-layer hidden-
state hooks without a custom forward-pass build, and our production
stack moved off vLLM in May 2026. transformers gives us
`model.layers[L].register_forward_hook(...)` for free, at the cost of
~10× lower throughput. Acceptable for research-velocity vector
training and Phase 1 ablation eval; not for production stress.

Public surface:
    from drydock.steering.sidecar.server import build_app
    app = build_app()                     # FastAPI app, lazy model load
    # Then: uvicorn ...:app --port 8002

CLI:
    bash scripts/start_steering_sidecar.sh
    bash scripts/start_steering_sidecar.sh --port 8002 --model /path/to/weights

See DEEP_NOIR_PRD.md for the architecture, training pipeline, and
milestones.
"""
