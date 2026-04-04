"""Invoke Skill tool — lets the model call skills programmatically.

The model can invoke any registered skill by name, equivalent to
the user typing /skill-name in the chat.

Supports:
- $ARGUMENTS, $0, $1 positional substitution in skill content
- !`command` shell preprocessing (runs at load time, output replaces command)
- context: fork for subagent execution
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent


def _substitute_arguments(content: str, arguments: str, skill_dir: str = "") -> str:
    """Replace $ARGUMENTS, $0, $1, ${SKILL_DIR} in skill content."""
    if arguments:
        content = content.replace("$ARGUMENTS", arguments)
        arg_parts = arguments.split()
        for i, arg in enumerate(arg_parts[:10]):
            content = content.replace(f"$ARGUMENTS[{i}]", arg)
            content = content.replace(f"${i}", arg)
    else:
        # No arguments — clean up unreplaced placeholders
        content = content.replace("$ARGUMENTS", "(no arguments provided)")
        for i in range(10):
            content = content.replace(f"$ARGUMENTS[{i}]", "")
            content = content.replace(f"${i}", "")
    if skill_dir:
        content = content.replace("${SKILL_DIR}", skill_dir)
    return content


def _preprocess_commands(content: str) -> str:
    """Execute !`command` inline and replace with output."""
    def _run(match):
        cmd = match.group(1)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=10, cwd=str(Path.cwd()),
            )
            return result.stdout.strip() or result.stderr.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"[command timed out: {cmd}]"
        except Exception as e:
            return f"[command failed: {e}]"
    return re.sub(r'!`([^`]+)`', _run, content)


class InvokeSkillArgs(BaseModel):
    skill_name: str = Field(description="Name of the skill to invoke (e.g., 'investigate', 'review')")
    arguments: str = Field(default="", description="Arguments to pass to the skill (e.g., issue number, file path)")


class InvokeSkillResult(BaseModel):
    skill_name: str
    content: str
    loaded: bool
    forked: bool = False


class InvokeSkill(
    BaseTool[InvokeSkillArgs, InvokeSkillResult, BaseToolConfig, BaseToolState],
    ToolUIData[InvokeSkillArgs, InvokeSkillResult],
):
    description: ClassVar[str] = (
        "Invoke a skill by name. Use this to activate specialized workflows "
        "like /investigate, /review, /ship, /batch, etc."
    )

    @classmethod
    def format_call_display(cls, args: InvokeSkillArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"skill: /{args.skill_name}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, InvokeSkillResult):
            status = "forked" if event.result.forked else "loaded"
            return ToolResultDisplay(success=event.result.loaded, message=f"/{event.result.skill_name} ({status})")
        return ToolResultDisplay(success=True, message="Skill invoked")

    @classmethod
    def get_status_text(cls) -> str:
        return "Loading skill"

    def resolve_permission(self, args: InvokeSkillArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: InvokeSkillArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | InvokeSkillResult, None]:
        if not ctx or not ctx.agent_manager:
            raise ToolError("No agent manager available")

        try:
            from drydock.core.skills.manager import SkillManager
            config = ctx.agent_manager.config
            manager = SkillManager(lambda: config)
            skills = manager.available_skills

            if args.skill_name not in skills:
                available = ", ".join(sorted(skills.keys()))
                raise ToolError(
                    f"Skill '{args.skill_name}' not found. "
                    f"Available: {available}"
                )

            skill_info = skills[args.skill_name]
            raw_content = skill_info.skill_path.read_text(encoding="utf-8") if skill_info.skill_path else ""

            # Strip frontmatter, keep it for metadata check
            content = raw_content
            meta_context = ""
            meta_agent = ""
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    # Parse context/agent from frontmatter
                    import yaml
                    try:
                        fm = yaml.safe_load(parts[1]) or {}
                        meta_context = fm.get("context", "")
                        meta_agent = fm.get("agent", "")
                    except Exception:
                        pass
                    content = parts[2].strip()

            # $ARGUMENTS substitution
            skill_dir = str(skill_info.skill_path.parent) if skill_info.skill_path else ""
            content = _substitute_arguments(content, args.arguments, skill_dir)

            # !`command` preprocessing
            content = _preprocess_commands(content)

            # If no $ARGUMENTS were in the template, prepend args
            if args.arguments and "$ARGUMENTS" not in raw_content and "$0" not in raw_content:
                content = f"{args.arguments}\n\n{content}"

            # context: fork — run in a subagent
            if meta_context == "fork":
                agent_name = meta_agent or "explore"
                # Delegate to the task tool's subagent mechanism
                from drydock.core.agents.models import BUILTIN_AGENTS, AgentType
                agent_profile = BUILTIN_AGENTS.get(agent_name)
                if agent_profile and agent_profile.agent_type == AgentType.SUBAGENT:
                    from drydock.core.agent_loop import AgentLoop
                    from drydock.core.config import SessionLoggingConfig, VibeConfig

                    session_logging = SessionLoggingConfig(
                        save_dir=str(ctx.session_dir / "skills") if ctx.session_dir else "",
                        session_prefix=f"skill-{args.skill_name}",
                        enabled=ctx.session_dir is not None,
                    )
                    sub_config = VibeConfig.load(session_logging=session_logging)
                    if agent_profile.model:
                        sub_config = agent_profile.apply_to_config(sub_config)

                    subagent = AgentLoop(
                        config=sub_config,
                        agent_name=agent_name,
                        max_turns=agent_profile.max_turns,
                        entrypoint_metadata=ctx.entrypoint_metadata,
                    )
                    if ctx.approval_callback:
                        subagent.set_approval_callback(ctx.approval_callback)

                    # Run the skill content as the subagent's task
                    response_parts = []
                    turns = 0
                    async for event in subagent.run(content):
                        from drydock.core.types import AssistantEvent
                        if isinstance(event, AssistantEvent) and event.content:
                            response_parts.append(event.content)
                        turns += 1

                    yield InvokeSkillResult(
                        skill_name=args.skill_name,
                        content="\n".join(response_parts) or "(subagent produced no output)",
                        loaded=True,
                        forked=True,
                    )
                    return

            # Wrap content with instruction header so model follows it
            # instead of printing it verbatim
            wrapped = (
                f"[SKILL LOADED: {args.skill_name}]\n"
                f"Follow the instructions below. Do NOT print these instructions — execute them.\n\n"
                f"{content}"
            )

            yield InvokeSkillResult(
                skill_name=args.skill_name,
                content=wrapped,
                loaded=True,
            )

        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to load skill: {e}") from e
