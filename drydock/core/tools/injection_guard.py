"""Prompt injection detection for file write operations.

Scans content being written to files for common injection patterns:
- Role override attempts ("You are now...", "Ignore previous instructions")
- System prompt extraction ("Show me your system prompt")
- Invisible Unicode characters used to hide instructions
- Base64 encoded instructions
- Attempts to modify DryDock config/state files

Inspired by GSD's prompt-guard approach.
"""

from __future__ import annotations

import base64
import logging
import re

logger = logging.getLogger(__name__)

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("role_override", re.compile(
        r"(?:you are now|act as|pretend to be|ignore (?:all )?previous|"
        r"disregard (?:all )?(?:prior|previous)|forget (?:all )?(?:prior|previous)|"
        r"new instructions|override (?:system|instructions))",
        re.IGNORECASE,
    )),
    ("system_prompt_leak", re.compile(
        r"(?:show (?:me )?your (?:system )?prompt|print your instructions|"
        r"reveal your (?:system )?(?:prompt|instructions)|"
        r"what are your (?:system )?instructions)",
        re.IGNORECASE,
    )),
    ("hidden_instruction", re.compile(
        r"(?:<!-- (?:SYSTEM|INSTRUCTION|HIDDEN|INJECT)|"
        r"\[INST\]|\[/INST\]|<\|im_start\|>|<\|system\|>)",
        re.IGNORECASE,
    )),
]

# Invisible Unicode characters that can hide instructions
_INVISIBLE_CHARS = set(
    "\u200b"  # Zero-width space
    "\u200c"  # Zero-width non-joiner
    "\u200d"  # Zero-width joiner
    "\u2060"  # Word joiner
    "\u2062"  # Invisible times
    "\u2063"  # Invisible separator
    "\u2064"  # Invisible plus
    "\ufeff"  # Zero-width no-break space (BOM)
    "\u00ad"  # Soft hyphen
    "\u034f"  # Combining grapheme joiner
    "\u061c"  # Arabic letter mark
    "\u180e"  # Mongolian vowel separator
)


def check_content_for_injection(content: str, file_path: str = "") -> str | None:
    """Check content for prompt injection patterns.

    Returns a warning message if injection is detected, None otherwise.
    This is advisory — it warns but does not block the write.
    """
    if not content:
        return None

    warnings: list[str] = []

    # Check for injection patterns
    for pattern_name, pattern in _INJECTION_PATTERNS:
        if match := pattern.search(content):
            warnings.append(f"Suspicious pattern '{pattern_name}': '{match.group()[:50]}'")

    # Check for invisible Unicode
    invisible_found = [c for c in content if c in _INVISIBLE_CHARS]
    if len(invisible_found) > 3:
        warnings.append(f"Found {len(invisible_found)} invisible Unicode characters")

    # Check for base64 encoded blocks that might contain instructions
    b64_pattern = re.compile(r'[A-Za-z0-9+/]{40,}={0,2}')
    for match in b64_pattern.finditer(content):
        try:
            decoded = base64.b64decode(match.group()).decode('utf-8', errors='ignore')
            # Check if the decoded content looks like instructions
            if any(kw in decoded.lower() for kw in ['ignore', 'override', 'system', 'instruction', 'you are']):
                warnings.append(f"Base64 encoded block may contain hidden instructions")
                break
        except Exception:
            pass

    # Check for attempts to modify DryDock internals
    if file_path:
        protected = ['.drydock/config.toml', '.drydock/.env', 'drydock/core/', 'CLAUDE.md']
        for p in protected:
            if p in file_path:
                # Not injection per se, but worth flagging
                logger.info("Write to protected path: %s", file_path)

    if warnings:
        msg = "INJECTION WARNING: " + "; ".join(warnings)
        logger.warning("Injection guard triggered for %s: %s", file_path, msg)
        return msg

    return None
