"""Invoke Skill tool — lets the model call skills programmatically.

The model can invoke any registered skill by name, equivalent to
the user typing /skill-name in the chat.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool, BaseToolConfig, BaseToolState, InvokeContext, ToolError, ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent, ToolResultEvent


class InvokeSkillArgs(BaseModel):
    skill_name: str = Field(description="Name of the skill to invoke (e.g., 'investigate', 'review')")
    arguments: str = Field(default="", description="Arguments to pass to the skill")


class InvokeSkillResult(BaseModel):
    skill_name: str
    content: str
    loaded: bool


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
            return ToolResultDisplay(success=event.result.loaded, message=f"/{event.result.skill_name}")
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

        # Find the skill
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
            content = skill_info.skill_path.read_text(encoding="utf-8") if skill_info.skill_path else ""

            # Strip frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                content = parts[2].strip() if len(parts) >= 3 else content

            # Prepend arguments if provided
            if args.arguments:
                content = f"{args.arguments}\n\n{content}"

            yield InvokeSkillResult(
                skill_name=args.skill_name,
                content=content,
                loaded=True,
            )

        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to load skill: {e}") from e
