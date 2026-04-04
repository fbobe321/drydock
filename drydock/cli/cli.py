from __future__ import annotations

import argparse
from pathlib import Path
import sys

from rich import print as rprint
import tomli_w

from drydock import __version__
from drydock.cli.textual_ui.app import run_textual_ui
from drydock.core.agent_loop import AgentLoop
from drydock.core.agents.models import BuiltinAgentName
from drydock.core.config import (
    MissingAPIKeyError,
    MissingPromptFileError,
    VibeConfig,
    load_dotenv_values,
)
from drydock.core.config.harness_files import get_harness_files_manager
from drydock.core.logger import logger
from drydock.core.paths import HISTORY_FILE
from drydock.core.programmatic import run_programmatic
from drydock.core.session.session_loader import SessionLoader
from drydock.core.types import EntrypointMetadata, LLMMessage, OutputFormat, Role
from drydock.core.utils import ConversationLimitException
from drydock.setup.onboarding import run_onboarding


def get_initial_agent_name(args: argparse.Namespace) -> str:
    if args.prompt is not None and args.agent == BuiltinAgentName.DEFAULT:
        return BuiltinAgentName.AUTO_APPROVE
    return args.agent


def get_prompt_from_stdin() -> str | None:
    if sys.stdin.isatty():
        return None
    try:
        if content := sys.stdin.read().strip():
            sys.stdin = sys.__stdin__ = open("/dev/tty")
            return content
    except KeyboardInterrupt:
        pass
    except OSError:
        return None

    return None


def load_config_or_exit() -> VibeConfig:
    try:
        return VibeConfig.load()
    except MissingAPIKeyError:
        run_onboarding()
        return VibeConfig.load()
    except MissingPromptFileError as e:
        rprint(f"[yellow]Invalid system prompt id: {e}[/]")
        sys.exit(1)
    except ValueError as e:
        rprint(f"[yellow]{e}[/]")
        sys.exit(1)


def bootstrap_config_files() -> None:
    mgr = get_harness_files_manager()
    config_file = mgr.user_config_file
    if not config_file.exists():
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with config_file.open("wb") as f:
                tomli_w.dump(VibeConfig.create_default(), f)
        except Exception as e:
            rprint(f"[yellow]Could not create default config file: {e}[/]")

    history_file = HISTORY_FILE.path
    if not history_file.exists():
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text("Hello Drydock!\n", "utf-8")
        except Exception as e:
            rprint(f"[yellow]Could not create history file: {e}[/]")


def load_session(
    args: argparse.Namespace, config: VibeConfig
) -> tuple[list[LLMMessage], Path] | None:
    if not args.continue_session and not args.resume:
        return None

    if not config.session_logging.enabled:
        rprint(
            "[red]Session logging is disabled. "
            "Enable it in config to use --continue or --resume[/]"
        )
        sys.exit(1)

    session_to_load = None
    if args.continue_session:
        session_to_load = SessionLoader.find_latest_session(config.session_logging)
        if not session_to_load:
            rprint(
                f"[red]No previous sessions found in "
                f"{config.session_logging.save_dir}[/]"
            )
            sys.exit(1)
    else:
        session_to_load = SessionLoader.find_session_by_id(
            args.resume, config.session_logging
        )
        if not session_to_load:
            rprint(
                f"[red]Session '{args.resume}' not found in "
                f"{config.session_logging.save_dir}[/]"
            )
            sys.exit(1)

    try:
        loaded_messages, _ = SessionLoader.load_session(session_to_load)
        return loaded_messages, session_to_load
    except Exception as e:
        rprint(f"[red]Failed to load session: {e}[/]")
        sys.exit(1)


def _resume_previous_session(
    agent_loop: AgentLoop, loaded_messages: list[LLMMessage], session_path: Path
) -> None:
    non_system_messages = [msg for msg in loaded_messages if msg.role != Role.system]
    agent_loop.messages.extend(non_system_messages)

    _, metadata = SessionLoader.load_session(session_path)
    session_id = metadata.get("session_id", agent_loop.session_id)
    agent_loop.session_id = session_id
    agent_loop.session_logger.resume_existing_session(session_id, session_path)

    logger.info(
        "Resumed session %s with %d messages", session_id, len(non_system_messages)
    )


def run_cli(args: argparse.Namespace) -> None:
    load_dotenv_values()
    bootstrap_config_files()

    if args.setup:
        run_onboarding()
        sys.exit(0)

    try:
        initial_agent_name = get_initial_agent_name(args)
        config = load_config_or_exit()

        if args.enabled_tools:
            config.enabled_tools = args.enabled_tools

        loaded_session = load_session(args, config)

        stdin_prompt = get_prompt_from_stdin()

        # -p flag feeds prompt into the TUI (headless mode removed)
        if args.prompt is not None:
            if not hasattr(args, 'initial_prompt') or not args.initial_prompt:
                args.initial_prompt = args.prompt or stdin_prompt

        # Disable problematic tools for models that can't handle complex schemas
        try:
            active = config.get_active_model()
            if "gemma" in active.name.lower():
                config.disabled_tools = [
                    *config.disabled_tools,
                    "ask_user_question",
                    "todo",
                    "task_create",
                    "task_update",
                    "task",
                    "invoke_skill",
                    "tool_search",
                ]
        except (ValueError, AttributeError):
            pass

        # Disable streaming for Gemma 4 — streaming tool call accumulation
        # produces empty arguments (14 write_file calls → 0 files)
        use_streaming = True
        try:
            if "gemma" in config.get_active_model().name.lower():
                use_streaming = False
        except Exception:
            pass

        agent_loop = AgentLoop(
            config,
            agent_name=initial_agent_name,
            enable_streaming=use_streaming,
            entrypoint_metadata=EntrypointMetadata(
                agent_entrypoint="cli",
                agent_version=__version__,
                client_name="drydock_cli",
                client_version=__version__,
            ),
        )

        if loaded_session:
            _resume_previous_session(agent_loop, *loaded_session)

        run_textual_ui(
            agent_loop=agent_loop,
            initial_prompt=args.initial_prompt or stdin_prompt,
            teleport_on_start=args.teleport,
            )

    except (KeyboardInterrupt, EOFError):
        rprint("\n[dim]Bye![/]")
        sys.exit(0)
