"""First-launch local-LLM autodetection.

Drydock targets local models, so on first run — when there's no config yet — we
probe the usual localhost ports for an OpenAI-compatible server and wire up the
first one we find. No cloud / API-key prompt: this is an air-gapped-first tool.

Each candidate is hit at <base>/models with a short timeout; a parseable JSON
response means a live server. All network errors are swallowed (a probe must
never crash startup), so detection degrades to "nothing found".

All logic original to Drydock.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# (provider-key, base_url) for the servers we know how to talk to. Order is the
# preference order when several are running.
CANDIDATES: list[tuple[str, str]] = [
    ("vllm", "http://localhost:8000/v1"),       # vLLM / llama.cpp (our default)
    ("ollama", "http://localhost:11434/v1"),
    ("lmstudio", "http://localhost:1234/v1"),
]


def probe_endpoint(base_url: str, timeout: float = 0.5) -> list[str] | None:
    """Return the model ids served at base_url, or None if unreachable.

    A reachable-but-empty server returns [] (still a hit); only a connection
    failure / bad response returns None.
    """
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    # OpenAI shape: {"data": [{"id": ...}]}; Ollama: {"models": [{"name": ...}]}
    items = data.get("data") or data.get("models") or []
    ids = [it.get("id") or it.get("name", "") for it in items if isinstance(it, dict)]
    return [i for i in ids if i]


def detect_local_llms(timeout: float = 0.5) -> list[dict]:
    """Probe all known candidates; return [{provider, base_url, models}] for
    each that responds, in preference order."""
    found = []
    for provider, base_url in CANDIDATES:
        models = probe_endpoint(base_url, timeout)
        if models is not None:
            found.append({"provider": provider, "base_url": base_url, "models": models})
    return found


def onboarding_message(found: list[dict]) -> str:
    """The line(s) shown on first launch, summarizing what was detected."""
    if not found:
        ports = ", ".join(b.split("//")[1] for _, b in CANDIDATES)
        return (
            "No local LLM detected (" + ports + "). Point Drydock at your model "
            "right here:\n"
            "  /model url <http://localhost:8000/v1>     then     /model <model-name>\n"
            "(saved to ~/.drydock/config.toml). Or start a local server "
            "(llama.cpp / vLLM / Ollama / LM Studio) and restart."
        )
    best = found[0]
    model = best["models"][0] if best["models"] else "?"
    line = f"⚓ Detected {best['provider']} at {best['base_url']} (model: {model}). Using it."
    if len(found) > 1:
        others = ", ".join(f["provider"] for f in found[1:])
        line += f"  Also available: {others} (switch with /model or config.toml)."
    return line
