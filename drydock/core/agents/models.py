from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING, Any

from drydock.core.paths import PLANS_DIR

if TYPE_CHECKING:
    from drydock.core.config import VibeConfig


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class AgentSafety(StrEnum):
    SAFE = auto()
    NEUTRAL = auto()
    DESTRUCTIVE = auto()
    YOLO = auto()


class AgentType(StrEnum):
    AGENT = auto()
    SUBAGENT = auto()


class BuiltinAgentName(StrEnum):
    DEFAULT = "default"
    CHAT = "chat"
    PLAN = "plan"
    ACCEPT_EDITS = "accept-edits"
    AUTO_APPROVE = "auto-approve"
    EXPLORE = "explore"
    DIAGNOSTIC = "diagnostic"
    PLANNER = "planner"
    BUILDER = "builder"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    display_name: str
    description: str
    safety: AgentSafety
    agent_type: AgentType = AgentType.AGENT
    overrides: dict[str, Any] = field(default_factory=dict)
    # New fields for feature parity with Claude Code
    model: str = ""  # Per-subagent model selection (e.g., "gemini-2.5-pro")
    max_turns: int | None = None  # Per-subagent turn limit
    background: bool = False  # Run in background (non-blocking)
    isolation: str = ""  # "worktree" for git worktree isolation
    initial_prompt: str = ""  # Auto-run this prompt on start
    allowed_tools: list[str] = field(default_factory=list)  # Restrict tools
    hooks: list[str] = field(default_factory=list)  # Hook configs

    def apply_to_config(self, base: VibeConfig) -> VibeConfig:
        from drydock.core.config import VibeConfig as VC

        merged = _deep_merge(base.model_dump(), self.overrides)
        # Apply per-subagent model if set
        if self.model:
            merged["active_model"] = self.model
        return VC.model_validate(merged)

    @classmethod
    def from_toml(cls, path: Path) -> AgentProfile:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return cls(
            name=path.stem,
            display_name=data.pop("display_name", path.stem.replace("-", " ").title()),
            description=data.pop("description", ""),
            safety=AgentSafety(data.pop("safety", AgentSafety.NEUTRAL)),
            agent_type=AgentType(data.pop("agent_type", AgentType.AGENT)),
            model=data.pop("model", ""),
            max_turns=data.pop("max_turns", None),
            background=data.pop("background", False),
            isolation=data.pop("isolation", ""),
            initial_prompt=data.pop("initial_prompt", ""),
            allowed_tools=data.pop("allowed_tools", []),
            hooks=data.pop("hooks", []),
            overrides=data,
        )

    @classmethod
    def from_markdown(cls, path: Path) -> AgentProfile:
        """Load agent profile from Markdown with YAML frontmatter."""
        import yaml
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            raise ValueError(f"No YAML frontmatter in {path}")
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid frontmatter in {path}")
        meta = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()

        return cls(
            name=meta.get("name", path.stem),
            display_name=meta.get("display_name", meta.get("name", path.stem)),
            description=meta.get("description", body[:200]),
            safety=AgentSafety(meta.get("safety", AgentSafety.NEUTRAL)),
            agent_type=AgentType(meta.get("agent_type", AgentType.SUBAGENT)),
            model=meta.get("model", ""),
            max_turns=meta.get("max_turns"),
            background=meta.get("background", False),
            isolation=meta.get("isolation", ""),
            initial_prompt=meta.get("initial_prompt", ""),
            allowed_tools=meta.get("allowed_tools") or meta.get("allowed-tools") or [],
            hooks=meta.get("hooks", []),
            overrides=meta.get("overrides", {}),
        )


CHAT_AGENT_TOOLS = ["grep", "read_file", "ask_user_question", "task"]
DIAGNOSTIC_AGENT_TOOLS = ["grep", "read_file", "bash"]


def _plan_overrides() -> dict[str, Any]:
    plans_pattern = str(PLANS_DIR.path / "*")
    return {
        "tools": {
            "write_file": {"permission": "never", "allowlist": [plans_pattern]},
            "search_replace": {"permission": "never", "allowlist": [plans_pattern]},
        }
    }


DEFAULT = AgentProfile(
    BuiltinAgentName.DEFAULT,
    "Default",
    "Requires approval for tool executions",
    AgentSafety.NEUTRAL,
)
PLAN = AgentProfile(
    BuiltinAgentName.PLAN,
    "Plan",
    "Read-only agent for exploration and planning",
    AgentSafety.SAFE,
    overrides=_plan_overrides(),
)
CHAT = AgentProfile(
    BuiltinAgentName.CHAT,
    "Chat",
    "Read-only conversational mode for questions and discussions",
    AgentSafety.SAFE,
    overrides={"auto_approve": True, "enabled_tools": CHAT_AGENT_TOOLS},
)
ACCEPT_EDITS = AgentProfile(
    BuiltinAgentName.ACCEPT_EDITS,
    "Accept Edits",
    "Auto-approves file edits only",
    AgentSafety.DESTRUCTIVE,
    overrides={
        "tools": {
            "write_file": {"permission": "always"},
            "search_replace": {"permission": "always"},
        }
    },
)
AUTO_APPROVE = AgentProfile(
    BuiltinAgentName.AUTO_APPROVE,
    "Auto Approve",
    "Auto-approves all tool executions",
    AgentSafety.YOLO,
    overrides={"auto_approve": True},
)

EXPLORE = AgentProfile(
    name=BuiltinAgentName.EXPLORE,
    display_name="Explore",
    description=(
        "Read-only subagent for codebase exploration. Use when you need to "
        "understand a project with 3+ files, map architecture, or find where "
        "a function/class is defined. Has grep and read_file only — cannot edit."
    ),
    safety=AgentSafety.SAFE,
    agent_type=AgentType.SUBAGENT,
    max_turns=60,
    overrides={"enabled_tools": ["grep", "read_file", "glob"], "system_prompt_id": "explore"},
)

DIAGNOSTIC = AgentProfile(
    name=BuiltinAgentName.DIAGNOSTIC,
    display_name="Diagnostic",
    description=(
        "Subagent that analyzes test failures, error traces, and runtime issues. "
        "Use when tests fail and you need to understand WHY before fixing. "
        "Can run tests via bash and read source code."
    ),
    safety=AgentSafety.SAFE,
    agent_type=AgentType.SUBAGENT,
    max_turns=80,
    overrides={
        "enabled_tools": ["grep", "read_file", "bash", "glob",
                          "web_search", "web_fetch"],
        "system_prompt_id": "diagnostic",
    },
)

PLANNER = AgentProfile(
    name=BuiltinAgentName.PLANNER,
    display_name="Planner",
    description=(
        "Read-only subagent that creates implementation plans before coding. "
        "Use for complex changes that touch multiple modules — the planner "
        "identifies target files, dependencies, and change order."
    ),
    safety=AgentSafety.SAFE,
    agent_type=AgentType.SUBAGENT,
    max_turns=40,
    overrides={
        "enabled_tools": ["grep", "read_file", "glob"],
        "system_prompt_id": "planner",
    },
)

BUILDER = AgentProfile(
    name=BuiltinAgentName.BUILDER,
    display_name="Builder",
    description=(
        "Subagent that creates an entire Python package from a PRD. Use this "
        "when the user gives you a PRD with 4+ files to write — delegating "
        "keeps the main agent's context small and lets the builder iterate "
        "in its own scratch space. Has read_file, write_file, search_replace, "
        "glob, and bash. Returns a short summary listing the files created "
        "and whether `python3 -m <pkg> --help` worked. Up to 200 turns."
    ),
    safety=AgentSafety.DESTRUCTIVE,
    agent_type=AgentType.SUBAGENT,
    max_turns=200,
    overrides={
        "enabled_tools": [
            "read_file", "write_file", "search_replace",
            "glob", "grep", "bash",
            # web tools — when stuck on a difficult problem, the model can
            # google for examples, error-message solutions, or API docs.
            "web_search", "web_fetch",
        ],
        "system_prompt_id": "builder",
        "tools": {
            "write_file": {"permission": "always"},
            "search_replace": {"permission": "always"},
            "bash": {"permission": "always"},
            "web_search": {"permission": "always"},
            "web_fetch": {"permission": "always"},
        },
    },
)

BUILTIN_AGENTS: dict[str, AgentProfile] = {
    BuiltinAgentName.DEFAULT: DEFAULT,
    BuiltinAgentName.PLAN: PLAN,
    BuiltinAgentName.ACCEPT_EDITS: ACCEPT_EDITS,
    BuiltinAgentName.AUTO_APPROVE: AUTO_APPROVE,
    BuiltinAgentName.EXPLORE: EXPLORE,
    BuiltinAgentName.DIAGNOSTIC: DIAGNOSTIC,
    BuiltinAgentName.PLANNER: PLANNER,
    BuiltinAgentName.BUILDER: BUILDER,
}
