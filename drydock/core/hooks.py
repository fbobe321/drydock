"""Hook system for DryDock lifecycle events.

Hooks run shell scripts or Python callables at specific points
in the agent's workflow. They are deterministic — they always run,
unlike CLAUDE.md which is advisory.

Hook events:
- PreToolUse: Before any tool executes (can block with "deny")
- PostToolUse: After a tool completes
- SessionStart: When a new session begins
- SessionEnd: When session ends
- PreEdit: Before file write/edit (can block destructive patterns)
- PostEdit: After file is written/edited

Configure in ~/.drydock/hooks.json or .drydock/hooks.json:
{
  "hooks": [
    {
      "event": "PreToolUse",
      "command": "python3 /path/to/guard.py",
      "tools": ["bash", "write_file"]
    }
  ]
}
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum, auto
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(StrEnum):
    PRE_TOOL_USE = auto()
    POST_TOOL_USE = auto()
    POST_TOOL_USE_FAILURE = auto()
    SESSION_START = auto()
    SESSION_END = auto()
    PRE_EDIT = auto()
    POST_EDIT = auto()
    USER_PROMPT_SUBMIT = auto()
    STOP = auto()
    FILE_CHANGED = auto()
    CWD_CHANGED = auto()
    PRE_COMPACT = auto()
    POST_COMPACT = auto()
    SUBAGENT_START = auto()
    SUBAGENT_STOP = auto()


class HookDecision(StrEnum):
    ALLOW = auto()
    DENY = auto()
    ASK = auto()


@dataclass
class HookResult:
    decision: HookDecision = HookDecision.ALLOW
    message: str = ""


@dataclass
class HookConfig:
    event: HookEvent
    command: str
    tools: list[str] = field(default_factory=list)  # Empty = all tools
    timeout: int = 10


class HookManager:
    """Manages and executes lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: list[HookConfig] = []
        self._loaded = False

    def load_hooks(self, config_dir: Path | None = None) -> None:
        """Load hooks from config files."""
        if self._loaded:
            return
        self._loaded = True

        search_paths = []
        if config_dir:
            search_paths.append(config_dir / "hooks.json")

        # Check project and user dirs
        project_hooks = Path.cwd() / ".drydock" / "hooks.json"
        if project_hooks.exists():
            search_paths.insert(0, project_hooks)

        try:
            from drydock.core.paths import DRYDOCK_HOME
            user_hooks = DRYDOCK_HOME.path / "hooks.json"
            if user_hooks.exists():
                search_paths.append(user_hooks)
        except Exception:
            pass

        for path in search_paths:
            try:
                if path.exists():
                    data = json.loads(path.read_text())
                    for h in data.get("hooks", []):
                        self._hooks.append(HookConfig(
                            event=HookEvent(h["event"].lower().replace("-", "_")),
                            command=h["command"],
                            tools=h.get("tools", []),
                            timeout=h.get("timeout", 10),
                        ))
                    logger.info("Loaded %d hooks from %s", len(data.get("hooks", [])), path)
            except Exception as e:
                logger.warning("Failed to load hooks from %s: %s", path, e)

    def get_hooks(self, event: HookEvent, tool_name: str = "") -> list[HookConfig]:
        """Get hooks matching an event and optional tool name."""
        return [
            h for h in self._hooks
            if h.event == event
            and (not h.tools or tool_name in h.tools)
        ]

    async def run_hooks(
        self, event: HookEvent, tool_name: str = "", context: dict[str, Any] | None = None
    ) -> HookResult:
        """Execute all hooks for an event. Returns the most restrictive result."""
        hooks = self.get_hooks(event, tool_name)
        if not hooks:
            return HookResult()

        most_restrictive = HookResult()

        for hook in hooks:
            try:
                result = await self._execute_hook(hook, context or {})
                if result.decision == HookDecision.DENY:
                    return result  # Deny immediately
                if result.decision == HookDecision.ASK and most_restrictive.decision == HookDecision.ALLOW:
                    most_restrictive = result
            except Exception as e:
                logger.warning("Hook %s failed: %s", hook.command, e)

        return most_restrictive

    async def _execute_hook(self, hook: HookConfig, context: dict[str, Any]) -> HookResult:
        """Execute a single hook command."""
        import asyncio

        env = {
            **__import__("os").environ,
            "DRYDOCK_HOOK_EVENT": hook.event.value,
            "DRYDOCK_HOOK_CONTEXT": json.dumps(context, default=str),
        }

        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=hook.timeout)

            output = stdout.decode("utf-8", errors="replace").strip()

            # Parse output for decision
            if "DENY" in output.upper():
                return HookResult(decision=HookDecision.DENY, message=output)
            if "ASK" in output.upper():
                return HookResult(decision=HookDecision.ASK, message=output)

            return HookResult(decision=HookDecision.ALLOW, message=output)

        except asyncio.TimeoutError:
            logger.warning("Hook timed out: %s", hook.command)
            return HookResult()


# Global hook manager instance
hook_manager = HookManager()
