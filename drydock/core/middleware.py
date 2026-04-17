from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, Protocol

from drydock.core.agents import AgentProfile
from drydock.core.utils import DRYDOCK_WARNING_TAG

if TYPE_CHECKING:
    from drydock.core.config import DrydockConfig
    from drydock.core.types import AgentStats, MessageList


class MiddlewareAction(StrEnum):
    CONTINUE = auto()
    STOP = auto()
    COMPACT = auto()
    INJECT_MESSAGE = auto()


class ResetReason(StrEnum):
    STOP = auto()
    COMPACT = auto()


@dataclass
class ConversationContext:
    messages: MessageList
    stats: AgentStats
    config: DrydockConfig


@dataclass
class MiddlewareResult:
    action: MiddlewareAction = MiddlewareAction.CONTINUE
    message: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationMiddleware(Protocol):
    async def before_turn(self, context: ConversationContext) -> MiddlewareResult: ...

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None: ...


class TurnLimitMiddleware:
    def __init__(self, max_turns: int) -> None:
        self.max_turns = max_turns

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        if context.stats.steps - 1 >= self.max_turns:
            return MiddlewareResult(
                action=MiddlewareAction.STOP,
                reason=f"Turn limit of {self.max_turns} reached",
            )
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass


class PriceLimitMiddleware:
    def __init__(self, max_price: float) -> None:
        self.max_price = max_price

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        if context.stats.session_cost > self.max_price:
            return MiddlewareResult(
                action=MiddlewareAction.STOP,
                reason=f"Price limit exceeded: ${context.stats.session_cost:.4f} > ${self.max_price:.2f}",
            )
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        pass


class AutoCompactMiddleware:
    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        # Proactive trigger at 90% of threshold — fires one turn EARLIER
        # than the strict cutoff so the next response doesn't bloat past
        # the hard limit. With Gemma 4's 80K threshold this means compact
        # at ~72K, which catches the request before it includes a fresh
        # tool result that pushes past 80K.
        self.proactive_threshold = int(threshold * 0.9)
        self._last_proactive_fire_at_tokens = 0

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        # Hard threshold — always compact
        if context.stats.context_tokens >= self.threshold:
            import logging
            logging.getLogger("drydock").warning(
                "[AUTO-COMPACT] firing at %d tokens (threshold %d)",
                context.stats.context_tokens, self.threshold,
            )
            return MiddlewareResult(
                action=MiddlewareAction.COMPACT,
                metadata={
                    "old_tokens": context.stats.context_tokens,
                    "threshold": self.threshold,
                },
            )
        # Proactive — fire at 90% of threshold but at most once per
        # ~10K-token growth window so we don't spam.
        if (context.stats.context_tokens >= self.proactive_threshold
                and context.stats.context_tokens - self._last_proactive_fire_at_tokens >= 10_000):
            self._last_proactive_fire_at_tokens = context.stats.context_tokens
            import logging
            logging.getLogger("drydock").warning(
                "[AUTO-COMPACT proactive] firing at %d tokens (90%% of %d)",
                context.stats.context_tokens, self.threshold,
            )
            return MiddlewareResult(
                action=MiddlewareAction.COMPACT,
                metadata={
                    "old_tokens": context.stats.context_tokens,
                    "threshold": self.threshold,
                    "proactive": True,
                },
            )
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        self._last_proactive_fire_at_tokens = 0


class ContextWarningMiddleware:
    """Tiered context warnings inspired by GSD's context monitoring.

    Warns at multiple thresholds as context fills up:
    - 50% used: soft warning ("you're halfway through")
    - 65% used: moderate warning ("wrap up current task")
    - 75% used: critical warning ("finish NOW or compact")
    - 85% used: emergency ("stop exploring, make final edit")

    Debounced: only warns every 5 tool calls to avoid spamming.
    """

    # Tiers use absolute token ranges (more actionable than percentages —
    # context window size is fixed per model). Defaults tuned for Gemma 4
    # (131K max, where degradation starts near 80K per 2026-04-15 stress
    # data). Other models just see percentages of their own max.
    _TIERS = [
        (0.50, "soft", "{used:,} tokens used ({pct:.0f}% of {max:,}). Start wrapping up your current task."),
        (0.65, "moderate", "{used:,} tokens used ({pct:.0f}% of {max:,}). Stop exploring — finish your current edit and verify it works."),
        (0.75, "critical", "{used:,} tokens used ({pct:.0f}% of {max:,}) — CRITICAL. Finish this edit NOW. No more grep/read_file. Use /compact if you need to continue."),
        (0.85, "emergency", "{used:,} tokens used ({pct:.0f}% of {max:,}) — context nearly full. You MUST stop after this turn or the model will start emitting empty responses and run-on text (freq_penalty poisoning). Apply your fix with search_replace immediately or use /compact."),
    ]

    def __init__(
        self, threshold_percent: float = 0.5, max_context: int | None = None
    ) -> None:
        self.threshold_percent = threshold_percent
        self.max_context = max_context
        self._tier_warned: set[str] = set()
        self._calls_since_last_warn = 0
        self._debounce_interval = 5

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        max_context = self.max_context
        if max_context is None or max_context == 0:
            return MiddlewareResult()

        self._calls_since_last_warn += 1

        # Debounce: don't warn on every single turn
        if self._calls_since_last_warn < self._debounce_interval:
            return MiddlewareResult()

        pct_used = context.stats.context_tokens / max_context

        # Find the highest tier we've crossed but haven't warned about
        for threshold, tier_name, template in reversed(self._TIERS):
            if pct_used >= threshold and tier_name not in self._tier_warned:
                self._tier_warned.add(tier_name)
                self._calls_since_last_warn = 0

                warning_msg = f"<{DRYDOCK_WARNING_TAG}>{template.format(pct=pct_used * 100, used=context.stats.context_tokens, max=max_context)}</{DRYDOCK_WARNING_TAG}>"
                return MiddlewareResult(
                    action=MiddlewareAction.INJECT_MESSAGE, message=warning_msg
                )
                break

        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        self._tier_warned.clear()
        self._calls_since_last_warn = 0


def make_plan_agent_reminder(plan_file_path: str) -> str:
    return f"""<{DRYDOCK_WARNING_TAG}>Plan mode is active. You MUST NOT make any edits (except to the plan file below), run any non-readonly tools (including changing configs or making commits), or otherwise make any changes to the system. This supersedes any other instructions you have received.

## Plan File Info
Create or edit your plan at {plan_file_path} using the write_file and search_replace tools.
Build your plan incrementally by writing to or editing this file.
This is the only file you are allowed to edit. Make sure to create it early and edit as soon as you internally update your plan.

## Instructions
1. Research the user's query using read-only tools (grep, read_file, etc.)
2. If you are unsure about requirements or approach, use the ask_user_question tool to clarify before finalizing your plan
3. Write your plan to the plan file above
4. When your plan is complete, call the exit_plan_mode tool to request user approval and switch to implementation mode</{DRYDOCK_WARNING_TAG}>"""


PLAN_AGENT_EXIT = f"""<{DRYDOCK_WARNING_TAG}>Plan mode has ended. If you have a plan ready, you can now start executing it. If not, you can now use editing tools and make changes to the system.</{DRYDOCK_WARNING_TAG}>"""

CHAT_AGENT_REMINDER = f"""<{DRYDOCK_WARNING_TAG}>Chat mode is active. The user wants to have a conversation -- ask questions, get explanations, or discuss code and architecture. You MUST NOT make any edits, run any non-readonly tools, or otherwise make any changes to the system. This supersedes any other instructions you have received. Instead, you should:
1. Answer the user's questions directly and comprehensively
2. Explain code, concepts, or architecture as requested
3. Use read-only tools (grep, read_file) to look up relevant code when needed
4. Focus on being informative and conversational -- your response IS the deliverable, not a precursor to action</{DRYDOCK_WARNING_TAG}>"""

CHAT_AGENT_EXIT = f"""<{DRYDOCK_WARNING_TAG}>Chat mode has ended. You can now use editing tools and make changes to the system.</{DRYDOCK_WARNING_TAG}>"""


class ReadOnlyAgentMiddleware:
    def __init__(
        self,
        profile_getter: Callable[[], AgentProfile],
        agent_name: str,
        reminder: str | Callable[[], str],
        exit_message: str,
    ) -> None:
        self._profile_getter = profile_getter
        self._agent_name = agent_name
        self._reminder = reminder
        self.exit_message = exit_message
        self._was_active = False

    @property
    def reminder(self) -> str:
        return self._reminder() if callable(self._reminder) else self._reminder

    def _is_active(self) -> bool:
        return self._profile_getter().name == self._agent_name

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        is_active = self._is_active()
        was_active = self._was_active

        if was_active and not is_active:
            self._was_active = False
            return MiddlewareResult(
                action=MiddlewareAction.INJECT_MESSAGE, message=self.exit_message
            )

        if is_active and not was_active:
            self._was_active = True
            return MiddlewareResult(
                action=MiddlewareAction.INJECT_MESSAGE, message=self.reminder
            )

        self._was_active = is_active
        return MiddlewareResult()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        self._was_active = False


class MiddlewarePipeline:
    def __init__(self) -> None:
        self.middlewares: list[ConversationMiddleware] = []

    def add(self, middleware: ConversationMiddleware) -> MiddlewarePipeline:
        self.middlewares.append(middleware)
        return self

    def clear(self) -> None:
        self.middlewares.clear()

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        for mw in self.middlewares:
            mw.reset(reset_reason)

    async def run_before_turn(self, context: ConversationContext) -> MiddlewareResult:
        messages_to_inject = []

        for mw in self.middlewares:
            result = await mw.before_turn(context)
            if result.action == MiddlewareAction.INJECT_MESSAGE and result.message:
                messages_to_inject.append(result.message)
            elif result.action in {MiddlewareAction.STOP, MiddlewareAction.COMPACT}:
                return result
        if messages_to_inject:
            combined_message = "\n\n".join(messages_to_inject)
            return MiddlewareResult(
                action=MiddlewareAction.INJECT_MESSAGE, message=combined_message
            )

        return MiddlewareResult()
