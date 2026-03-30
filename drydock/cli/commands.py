from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Command:
    aliases: frozenset[str]
    description: str
    handler: str
    exits: bool = False


class CommandRegistry:
    def __init__(self, excluded_commands: list[str] | None = None) -> None:
        if excluded_commands is None:
            excluded_commands = []
        self.commands = {
            "help": Command(
                aliases=frozenset(["/help"]),
                description="Show help message",
                handler="_show_help",
            ),
            "config": Command(
                aliases=frozenset(["/config", "/model"]),
                description="Edit config settings",
                handler="_show_config",
            ),
            "reload": Command(
                aliases=frozenset(["/reload"]),
                description="Reload configuration from disk",
                handler="_reload_config",
            ),
            "clear": Command(
                aliases=frozenset(["/clear"]),
                description="Clear conversation history",
                handler="_clear_history",
            ),
            "log": Command(
                aliases=frozenset(["/log"]),
                description="Show path to current interaction log file",
                handler="_show_log_path",
            ),
            "compact": Command(
                aliases=frozenset(["/compact"]),
                description="Compact conversation history by summarizing",
                handler="_compact_history",
            ),
            "exit": Command(
                aliases=frozenset(["/exit"]),
                description="Exit the application",
                handler="_exit_app",
                exits=True,
            ),
            "terminal-setup": Command(
                aliases=frozenset(["/terminal-setup"]),
                description="Configure Shift+Enter for newlines",
                handler="_setup_terminal",
            ),
            "status": Command(
                aliases=frozenset(["/status"]),
                description="Display agent statistics",
                handler="_show_status",
            ),
            "teleport": Command(
                aliases=frozenset(["/teleport"]),
                description="Teleport session to Drydock cloud",
                handler="_teleport_command",
            ),
            "proxy-setup": Command(
                aliases=frozenset(["/proxy-setup"]),
                description="Configure proxy and SSL certificate settings",
                handler="_show_proxy_setup",
            ),
            "consult": Command(
                aliases=frozenset(["/consult"]),
                description="Ask a smarter model for advice (response visible to local model)",
                handler="_consult_command",
            ),
            "rewind": Command(
                aliases=frozenset(["/rewind"]),
                description="Undo the last assistant turn and its tool calls",
                handler="_rewind_command",
            ),
            "resume": Command(
                aliases=frozenset(["/resume", "/continue"]),
                description="Browse and resume past sessions",
                handler="_show_session_picker",
            ),
            "setup-model": Command(
                aliases=frozenset(["/setup-model", "/add-model"]),
                description="Configure a local LLM (vLLM, Ollama, LM Studio, etc.)",
                handler="_setup_model_command",
            ),
        }

        for command in excluded_commands:
            self.commands.pop(command, None)

        self._alias_map = {}
        for cmd_name, cmd in self.commands.items():
            for alias in cmd.aliases:
                self._alias_map[alias] = cmd_name

    def find_command(self, user_input: str) -> Command | None:
        cmd_name = self.get_command_name(user_input)
        return self.commands.get(cmd_name) if cmd_name else None

    def get_command_name(self, user_input: str) -> str | None:
        stripped = user_input.lower().strip()
        # Exact match first
        if stripped in self._alias_map:
            return self._alias_map[stripped]
        # Prefix match for commands that take arguments (e.g., /consult <question>)
        first_word = stripped.split()[0] if stripped else ""
        if first_word in self._alias_map:
            return self._alias_map[first_word]
        return None

    def get_command_args(self, user_input: str) -> str:
        """Extract arguments after the command name."""
        parts = user_input.strip().split(None, 1)
        return parts[1] if len(parts) > 1 else ""

    def get_help_text(self) -> str:
        lines: list[str] = [
            "### Keyboard Shortcuts",
            "",
            "- `Enter` Submit message",
            "- `Ctrl+J` / `Shift+Enter` Insert newline",
            "- `Escape` Interrupt agent or close dialogs",
            "- `Ctrl+C` Cancel current operation (double-tap to quit)",
            "- `Ctrl+G` Edit input in external editor",
            "- `Ctrl+O` Toggle tool output view",
            "- `Ctrl+Y` / `Ctrl+Shift+C` Copy selected text",
            "- `Shift+Tab` Toggle auto-approve mode",
            "- `Shift+Up/Down` Scroll chat history",
            "",
            "### Mouse",
            "",
            "- **Scroll wheel**: Scroll chat history up/down",
            "- **Shift + click-drag**: Select text for copy (bypasses app mouse capture)",
            "",
            "### Special Features",
            "",
            "- `!<command>` Execute bash command directly",
            "- `@path/to/file/` Autocompletes file paths",
            "",
            "### Commands",
            "",
        ]

        for cmd in self.commands.values():
            aliases = ", ".join(f"`{alias}`" for alias in sorted(cmd.aliases))
            lines.append(f"- {aliases}: {cmd.description}")
        return "\n".join(lines)
