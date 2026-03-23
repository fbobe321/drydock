from __future__ import annotations

import asyncio
import os
import signal
import sys

from vibe import __version__
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfig
from vibe.core.logger import logger
from vibe.core.output_formatters import create_formatter
from vibe.core.types import (
    AssistantEvent,
    ClientMetadata,
    EntrypointMetadata,
    LLMMessage,
    OutputFormat,
    Role,
)
from vibe.core.utils import ConversationLimitException

_DEFAULT_CLIENT_METADATA = ClientMetadata(name="vibe_programmatic", version=__version__)


def run_programmatic(
    config: VibeConfig,
    prompt: str,
    max_turns: int | None = None,
    max_price: float | None = None,
    output_format: OutputFormat = OutputFormat.TEXT,
    previous_messages: list[LLMMessage] | None = None,
    agent_name: str = BuiltinAgentName.AUTO_APPROVE,
    client_metadata: ClientMetadata = _DEFAULT_CLIENT_METADATA,
) -> str | None:
    formatter = create_formatter(output_format)

    agent_loop = AgentLoop(
        config,
        agent_name=agent_name,
        message_observer=formatter.on_message_added,
        max_turns=max_turns,
        max_price=max_price,
        enable_streaming=False,
        entrypoint_metadata=EntrypointMetadata(
            agent_entrypoint="programmatic",
            agent_version=__version__,
            client_name=client_metadata.name,
            client_version=client_metadata.version,
        ),
    )
    logger.info("USER: %s", prompt)

    def _force_exit_handler(signum: int, frame: object) -> None:
        """SIGALRM handler: force exit when async cleanup hangs."""
        os._exit(1)

    async def _async_run() -> str | None:
        try:
            if previous_messages:
                non_system_messages = [
                    msg for msg in previous_messages if not (msg.role == Role.system)
                ]
                agent_loop.messages.extend(non_system_messages)
                logger.info(
                    "Loaded %d messages from previous session", len(non_system_messages)
                )

            agent_loop.emit_new_session_telemetry()

            async for event in agent_loop.act(prompt):
                formatter.on_event(event)
                if isinstance(event, AssistantEvent) and event.stopped_by_middleware:
                    # Schedule force-exit in 10s — async generator cleanup can hang
                    # indefinitely when the generator is mid-LLM-call.
                    signal.signal(signal.SIGALRM, _force_exit_handler)
                    signal.alarm(10)
                    raise ConversationLimitException(event.content)

            return formatter.finalize()
        finally:
            try:
                await asyncio.wait_for(
                    agent_loop.telemetry_client.aclose(), timeout=5.0
                )
            except (asyncio.TimeoutError, Exception):
                pass

    try:
        return asyncio.run(_async_run())
    except ConversationLimitException:
        # Agent was stopped by middleware (loop detection, turn limit, etc.)
        # This is a normal exit — the agent did work, just got stopped.
        # Exit 0 so the harness knows a patch may have been generated.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
