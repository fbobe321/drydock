"""Auto-detection of a running local LLM server on first launch.

Drydock's product pitch is "works out of the box with vLLM/Ollama/LM Studio/
llama.cpp — no API key required." That promise was broken for fresh installs:
the default `active_model` was the Mistral cloud devstral-2, which fired a
`MissingAPIKeyError` and dropped the user into the onboarding flow. Users who
already had a local server running still had to walk through the onboarding
screens.

This module probes the four common local-LLM endpoints with a short
HTTP timeout. If any responds with an OpenAI-compatible `/models` listing,
we capture the api_base and the first model id, and the bootstrap path
writes a config that points at it directly. Net effect: a user with a
running local server gets a working drydock on first launch with zero
prompts.

The probe is kept dependency-free (stdlib `urllib`) so it cannot break
on import in environments where `httpx`/`requests` aren't yet installed.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


# (label, api_base) — order matters: try most-likely first.
# We try llama-server first (per the upstream PRD draft's recommended
# Q3_K_M setup), then Ollama (largest install base of the four), then
# vLLM (drydock's own dev stack), then LM Studio.
_CANDIDATE_ENDPOINTS: list[tuple[str, str]] = [
    ("llama.cpp", "http://127.0.0.1:8080/v1"),
    ("Ollama",    "http://127.0.0.1:11434/v1"),
    ("vLLM",      "http://127.0.0.1:8000/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
]

_PER_REQUEST_TIMEOUT_S = 0.8


@dataclass(frozen=True)
class LocalServerInfo:
    label: str          # "vLLM" / "Ollama" / etc — informational only
    api_base: str       # full URL ending in /v1
    model_name: str     # first model id reported by the server


def detect_local_llm() -> LocalServerInfo | None:
    """Probe the candidate endpoints; return the first one that answers.

    A "good" response is HTTP 200 from `<api_base>/models` returning JSON
    of the form `{"data": [{"id": "..."}, ...]}` (the OpenAI standard).
    Servers that don't expose this (e.g. raw Ollama on port 11434/api/*)
    are skipped — drydock needs the OpenAI-compatible surface anyway.

    Total worst-case wall time: ~3.2 s when all four endpoints are
    unreachable. In practice each socket connect to a closed port fails
    in single-digit ms, so an "all-miss" probe is typically <50 ms.
    """
    for label, api_base in _CANDIDATE_ENDPOINTS:
        info = _probe_one(label, api_base)
        if info is not None:
            return info
    return None


def _probe_one(label: str, api_base: str) -> LocalServerInfo | None:
    url = f"{api_base.rstrip('/')}/models"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_PER_REQUEST_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list) or not models:
        return None

    first = models[0]
    if not isinstance(first, dict):
        return None
    model_name = first.get("id")
    if not isinstance(model_name, str) or not model_name:
        return None

    return LocalServerInfo(label=label, api_base=api_base, model_name=model_name)


def patch_config_for_local(
    config: dict, info: LocalServerInfo
) -> None:
    """Mutate a default-config dict in place so the detected local server
    is the active model.

    Strategy: keep the existing `llamacpp` provider entry but point it at
    the detected api_base, and update the `local` model's name to the
    detected model id. Set `active_model = "local"`. This preserves the
    rest of the user's config (other providers, models, settings) — we
    only touch the entries that matter for the local path.
    """
    config["active_model"] = "local"

    providers = config.get("providers")
    if isinstance(providers, list):
        for provider in providers:
            if isinstance(provider, dict) and provider.get("name") == "llamacpp":
                provider["api_base"] = info.api_base
                # api_key_env_var stays empty — local server doesn't need one
                break

    models = config.get("models")
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict) and model.get("alias") == "local":
                model["name"] = info.model_name
                # When the detected backend is llama.cpp, bake in the
                # Gemma 4 anti-loop sampling recipe (article: i-built-a-
                # gemma-4-ai-agent-it-kept-looping). Temperature must be
                # 1.0; lower values reinforce loops on quantized GGUF.
                # Don't clobber user-defined values if config was already
                # touched.
                if info.label == "llama.cpp":
                    model.setdefault("temperature", 1.0)
                    # llama-server article recipe uses `-c 32768`. Match
                    # it here so DrydockConfig's auto-clamp lowers
                    # auto_compact_threshold to 28K (context_window-4K
                    # headroom) — otherwise context bloats past 32K and
                    # the server returns empty/garbage. User can raise
                    # this if they're running with a larger -c.
                    model.setdefault("context_window", 32_768)
                    model.setdefault("auto_compact_threshold", 28_000)
                    extra = model.setdefault("extra_params", {})
                    extra.setdefault("top_k", 40)
                    extra.setdefault("top_p", 0.95)
                    extra.setdefault("frequency_penalty", 1.1)
                    extra.setdefault("max_tokens", 2048)
                break
