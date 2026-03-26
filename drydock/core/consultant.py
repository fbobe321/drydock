"""Consultant agent — asks a configured model for single-turn advice.

The consultant is a READ-ONLY advisor:
- Uses a model from DryDock's own config (providers + models)
- NEVER calls tools, writes files, or runs commands
- Only receives a question and returns reasoning/advice
- Response is injected into context so the local model can see it

Configure in config.toml:
    consultant_model = "gemini-2.5-pro"

Or via CLI:
    drydock --consultant gemini-2.5-pro

Or via /consult slash command:
    /consult How should I fix this auth bug?
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


async def ask_consultant(
    question: str,
    config: object | None = None,
    model: str | None = None,
    conversation_history: str = "",
) -> str:
    """Ask the consultant model a question using DryDock's backend.

    Parameters
    ----------
    question : str
        The specific question to ask.
    config : VibeConfig, optional
        DryDock config with providers/models. If None, uses env vars.
    model : str, optional
        Model name override. If None, uses config.consultant_model or env var.

    Returns
    -------
    str
        The consultant's advice text, or an error message.
    """
    # Resolve model name
    model_name = model or os.environ.get("DRYDOCK_CONSULTANT_MODEL", "")
    if not model_name and config:
        model_name = getattr(config, "consultant_model", "")
    if not model_name:
        return ""

    history_section = ""
    if conversation_history:
        history_section = f"\nCONVERSATION CONTEXT (recent messages):\n{conversation_history[:3000]}\n"

    prompt = (
        "You are a senior software engineering consultant. An AI coding agent is "
        "asking for your advice. Give concise, actionable guidance. Do NOT provide "
        "code unless specifically asked — just say WHAT to do and WHERE to look.\n"
        f"{history_section}\n"
        f"QUESTION:\n{question}"
    )

    # Try to use DryDock's backend system
    if config:
        try:
            return await _ask_via_backend(prompt, model_name, config)
        except Exception as e:
            logger.warning("Consultant via backend failed: %s — falling back to httpx", e)

    # Fallback: direct API call
    try:
        return await _ask_via_httpx(prompt, model_name)
    except Exception as e:
        return f"(Consultant unavailable: {e})"


async def _ask_via_backend(prompt: str, model_name: str, config: object) -> str:
    """Use DryDock's configured backend to call the consultant model."""
    from drydock.core.config import ModelConfig, ProviderConfig
    from drydock.core.llm.backend.factory import BACKEND_FACTORY
    from drydock.core.types import LLMMessage, Role

    # Find the model and its provider in the config
    target_model: ModelConfig | None = None
    for m in config.models:
        if m.name == model_name or m.alias == model_name:
            target_model = m
            break

    if not target_model:
        raise ValueError(f"Model '{model_name}' not found in config. Available: {[m.name for m in config.models]}")

    # Find the provider
    target_provider: ProviderConfig | None = None
    for p in config.providers:
        if p.name == target_model.provider:
            target_provider = p
            break

    if not target_provider:
        raise ValueError(f"Provider '{target_model.provider}' not found for model '{model_name}'")

    # Create a one-shot backend
    backend = BACKEND_FACTORY[target_provider.backend](
        provider=target_provider,
        timeout=30,
    )

    messages = [LLMMessage(role=Role.user, content=prompt)]

    result = await backend.complete(
        model=target_model,
        messages=messages,
        temperature=0.3,
        tools=None,  # NO tools — read-only
        tool_choice=None,
        max_tokens=500,
    )

    return result.message.content or ""


async def _ask_via_httpx(prompt: str, model_name: str) -> str:
    """Fallback: direct API call via httpx for models not in config."""
    import httpx

    api_key = _detect_api_key(model_name)
    api_base = _detect_api_base(model_name)

    if not api_key:
        return f"(No API key found for '{model_name}')"

    verify_ssl = os.environ.get("DRYDOCK_INSECURE") != "1"
    async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
        response = await client.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.3,
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def _detect_api_key(model: str) -> str:
    model_lower = model.lower()
    if "gemini" in model_lower:
        return os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
    if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return os.environ.get("OPENAI_API_KEY", "")
    if "claude" in model_lower:
        return os.environ.get("ANTHROPIC_API_KEY", "")
    if "mistral" in model_lower or "devstral" in model_lower:
        return os.environ.get("MISTRAL_API_KEY", "")
    return os.environ.get("DRYDOCK_CONSULTANT_API_KEY", "")


def _detect_api_base(model: str) -> str:
    model_lower = model.lower()
    if "gemini" in model_lower:
        return "https://generativelanguage.googleapis.com/v1beta/openai"
    if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return "https://api.openai.com/v1"
    if "claude" in model_lower:
        return "https://api.anthropic.com/v1"
    if "mistral" in model_lower or "devstral" in model_lower:
        return os.environ.get("MISTRAL_API_BASE", "https://api.mistral.ai/v1")
    return os.environ.get("DRYDOCK_CONSULTANT_API_BASE", "http://localhost:8000/v1")


def is_consultant_available(config: object | None = None) -> bool:
    """Check if a consultant model is configured."""
    if os.environ.get("DRYDOCK_CONSULTANT_MODEL"):
        return True
    if config and getattr(config, "consultant_model", ""):
        return True
    return False
