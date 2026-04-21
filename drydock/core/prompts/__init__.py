from __future__ import annotations

import os
from enum import StrEnum, auto
from pathlib import Path

from drydock import DRYDOCK_ROOT

_PROMPTS_DIR = DRYDOCK_ROOT / "core" / "prompts"


def _resolve_prompt_path(name: str) -> Path:
    """Return the .md file for a prompt name.

    If DRYDOCK_PROMPTS_DIR is set and contains a matching file, use that
    override. Falls back to the shipped prompt. Used by the Meta-Harness
    research kernel (research/kernel.py) to swap in per-experiment
    prompt variants without mutating the installed package.

    Missing override file = use shipped (not an error) so experiments
    can mutate only gemma4 without also overriding every other prompt.
    """
    override_dir = os.environ.get("DRYDOCK_PROMPTS_DIR", "")
    if override_dir:
        candidate = Path(override_dir) / f"{name}.md"
        if candidate.is_file():
            return candidate
    return (_PROMPTS_DIR / name).with_suffix(".md")


class Prompt(StrEnum):
    @property
    def path(self) -> Path:
        return _resolve_prompt_path(self.value)

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8").strip()


class SystemPrompt(Prompt):
    CLI = auto()
    EXPLORE = auto()
    TESTS = auto()
    BUILDER = auto()
    PLANNER = auto()
    DIAGNOSTIC = auto()
    GEMMA4 = auto()


class UtilityPrompt(Prompt):
    COMPACT = auto()
    DANGEROUS_DIRECTORY = auto()
    PROJECT_CONTEXT = auto()


__all__ = ["SystemPrompt", "UtilityPrompt"]
