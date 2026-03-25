"""Consultant agent — asks a smarter model for single-turn advice.

The consultant is a READ-ONLY advisor:
- The local model stays in control of ALL tool calls
- The consultant NEVER calls tools, writes files, or runs commands
- It only receives a question and returns reasoning/advice
- Used when the local model is stuck, uncertain, or looping

Usage from agent_loop:
    advice = await ask_consultant("I found 3 files that match. Which one likely contains the bug?")

The consultant model is configured via:
- CLI: drydock --consultant gemini-2.5-pro
- Env: DRYDOCK_CONSULTANT_MODEL=gemini-2.5-pro
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def ask_consultant(
    question: str,
    context: str = "",
    model: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> str:
    """Ask the consultant model a question and get text advice back.

    Parameters
    ----------
    question : str
        The specific question to ask.
    context : str
        Optional context (e.g., file contents, error messages).
    model : str
        Model name. Defaults to DRYDOCK_CONSULTANT_MODEL env var.
    api_key : str
        API key. Auto-detected from common env vars if not provided.
    api_base : str
        API base URL. Auto-detected based on model name if not provided.

    Returns
    -------
    str
        The consultant's advice text, or an error message.
    """
    model = model or os.environ.get("DRYDOCK_CONSULTANT_MODEL", "")
    if not model:
        return ""

    # Auto-detect API key and base URL from model name
    api_key = api_key or _detect_api_key(model)
    api_base = api_base or _detect_api_base(model)

    if not api_key:
        logger.warning("No API key found for consultant model '%s'", model)
        return ""

    prompt = f"""You are a senior software engineering consultant. A junior AI coding agent is asking for your advice.
Give concise, actionable guidance. Do NOT provide code — just tell them WHAT to do and WHERE to look.

QUESTION:
{question}
"""
    if context:
        prompt += f"\nCONTEXT:\n{context[:2000]}\n"

    try:
        # Use OpenAI-compatible API (works with Gemini, OpenAI, Mistral, local models)
        verify_ssl = os.environ.get("DRYDOCK_INSECURE") != "1"
        async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
            response = await client.post(
                f"{api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                    # NO tools — consultant is read-only
                },
            )
            response.raise_for_status()
            data = response.json()
            advice = data["choices"][0]["message"]["content"]
            logger.info("Consultant (%s) responded: %s", model, advice[:100])
            return advice

    except Exception as e:
        logger.warning("Consultant call failed: %s", e)
        return f"(Consultant unavailable: {e})"


def _detect_api_key(model: str) -> str:
    """Auto-detect API key from environment based on model name."""
    model_lower = model.lower()

    if "gemini" in model_lower:
        return os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
    if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return os.environ.get("OPENAI_API_KEY", "")
    if "claude" in model_lower:
        return os.environ.get("ANTHROPIC_API_KEY", "")
    if "mistral" in model_lower or "devstral" in model_lower:
        return os.environ.get("MISTRAL_API_KEY", "")

    # Generic fallback
    return os.environ.get("DRYDOCK_CONSULTANT_API_KEY", "")


def _detect_api_base(model: str) -> str:
    """Auto-detect API base URL from model name."""
    model_lower = model.lower()

    if "gemini" in model_lower:
        return "https://generativelanguage.googleapis.com/v1beta/openai"
    if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return "https://api.openai.com/v1"
    if "claude" in model_lower:
        return "https://api.anthropic.com/v1"
    if "mistral" in model_lower or "devstral" in model_lower:
        return os.environ.get("MISTRAL_API_BASE", "https://api.mistral.ai/v1")

    # Try local vLLM
    return os.environ.get("DRYDOCK_CONSULTANT_API_BASE", "http://localhost:8000/v1")


def is_consultant_available() -> bool:
    """Check if a consultant model is configured."""
    return bool(os.environ.get("DRYDOCK_CONSULTANT_MODEL"))
