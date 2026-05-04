from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from typing import Any
from functools import lru_cache
import os
from pathlib import Path
import signal
import sys
from typing import ClassVar, Literal, final

from pydantic import BaseModel, Field, field_validator
from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolResultEvent, ToolStreamEvent
from drydock.core.utils import is_windows


@lru_cache(maxsize=1)
def _get_parser() -> Parser:
    return Parser(Language(tsbash.language()))


def _extract_commands(command: str) -> list[str]:
    parser = _get_parser()
    tree = parser.parse(command.encode("utf-8"))

    commands: list[str] = []

    def find_commands(node: Node) -> None:
        if node.type == "command":
            parts = []
            for child in node.children:
                if (
                    child.type
                    in {"command_name", "word", "string", "raw_string", "concatenation"}
                    and child.text is not None
                ):
                    parts.append(child.text.decode("utf-8"))
            if parts:
                commands.append(" ".join(parts))

        for child in node.children:
            find_commands(child)

    find_commands(tree.root_node)
    return commands


def _get_subprocess_encoding() -> str:
    if sys.platform == "win32":
        # Windows console uses OEM code page (e.g., cp850, cp1252)
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    return "utf-8"


def _get_shell_executable() -> str | None:
    if is_windows():
        return None
    return os.environ.get("SHELL")


def _get_conda_setup_script() -> str | None:
    """Find conda's shell setup script so conda activate works in non-interactive shells."""
    # Check common conda installation paths
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        conda_dir = Path(conda_exe).parent.parent
        setup = conda_dir / "etc" / "profile.d" / "conda.sh"
        if setup.exists():
            return str(setup)

    for candidate in [
        Path.home() / "miniconda3" / "etc" / "profile.d" / "conda.sh",
        Path.home() / "anaconda3" / "etc" / "profile.d" / "conda.sh",
        Path.home() / "miniforge3" / "etc" / "profile.d" / "conda.sh",
        Path("/opt/conda/etc/profile.d/conda.sh"),
        Path("/usr/local/conda/etc/profile.d/conda.sh"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def _get_base_env() -> dict[str, str]:
    base_env = {**os.environ, "CI": "true", "NONINTERACTIVE": "1", "NO_TTY": "1"}

    if is_windows():
        base_env["GIT_PAGER"] = "more"
        base_env["PAGER"] = "more"
    else:
        base_env["TERM"] = "dumb"
        base_env["DEBIAN_FRONTEND"] = "noninteractive"
        base_env["GIT_PAGER"] = "cat"
        base_env["PAGER"] = "cat"
        base_env["LESS"] = "-FX"
        base_env["LC_ALL"] = "en_US.UTF-8"

        # Enable conda in non-interactive shells by setting BASH_ENV
        # to source conda's setup script. Without this, `conda activate`
        # fails because it's a shell function defined in .bashrc.
        #
        # IMPORTANT: We also preserve the user's active conda env to avoid
        # interfering with other environments and losing their aliases.
        conda_sh = _get_conda_setup_script()
        if conda_sh:
            # Create a wrapper that sources conda.sh then re-activates the user's env
            active_env = os.environ.get("CONDA_DEFAULT_ENV", "")
            if active_env and active_env != "base":
                # Write a temp script that initializes conda AND activates the user's env
                import tempfile
                wrapper = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".sh", delete=False, prefix="drydock_conda_"
                )
                wrapper.write(f'source "{conda_sh}"\nconda activate "{active_env}" 2>/dev/null\n')
                wrapper.close()
                base_env["BASH_ENV"] = wrapper.name
            else:
                base_env["BASH_ENV"] = conda_sh

    return base_env


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return

    try:
        if sys.platform == "win32":
            try:
                subprocess_proc = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await subprocess_proc.wait()
            except (FileNotFoundError, OSError):
                proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

        await proc.wait()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _get_default_allowlist() -> list[str]:
    # Auto-accepted because they are read-only / non-destructive.
    # Each entry is a prefix — commands starting with any of these are
    # ALWAYS allowed. Parser splits at pipes/chains so `ls | rm` is
    # checked as two separate commands.
    common = [
        "echo", "find", "tree", "whoami",
        # Git read-only subcommands
        "git diff", "git log", "git status", "git show",
        "git branch", "git ls-files", "git grep", "git remote",
        "git rev-parse", "git blame", "git config --get",
        "git tag", "git stash list",
        # Search tools (read-only by design)
        "grep", "rg", "fd", "fdfind", "ag",
        # Text/file inspection
        "diff", "cmp", "sort", "uniq", "awk", "cut", "tr",
        "basename", "dirname", "realpath", "readlink",
        # System info (read-only)
        "date", "id", "hostname", "pwd", "env", "printenv",
        "du", "df", "ps", "free", "uptime",
        # Python project management — safe read/install operations
        "pip install", "pip list", "pip show", "pip freeze", "pip check",
        "pip install -e", "pip install -r",
        "conda install -y", "conda list", "conda info", "conda env list",
        "conda run", "conda create -y",
        "python -m pip", "python3 -m pip",
        "python -m pytest", "python3 -m pytest",
        "python -c", "python3 -c",
        "pytest", "make", "tox",
    ]

    if is_windows():
        return common + ["dir", "findstr", "more", "type", "ver", "where"]
    else:
        return common + [
            "cat",
            "file",
            "head",
            "ls",
            "pwd",
            "stat",
            "tail",
            "uname",
            "wc",
            "which",
        ]


def _get_default_denylist() -> list[str]:
    common = ["gdb", "pdb", "passwd"]

    if is_windows():
        return common + ["cmd /k", "powershell -NoExit", "pwsh -NoExit", "notepad"]
    else:
        return common + [
            "nano",
            "vim",
            "vi",
            "emacs",
            "bash -i",
            "sh -i",
            "zsh -i",
            "fish -i",
            "dash -i",
            "screen",
            "tmux",
        ]


def _get_default_denylist_standalone() -> list[str]:
    common = ["python", "python3", "ipython"]

    if is_windows():
        return common + ["cmd", "powershell", "pwsh", "notepad"]
    else:
        return common + ["bash", "sh", "nohup", "vi", "vim", "emacs", "nano", "su"]


def _merge_with_defaults(user_value: Any, defaults_fn: Callable[[], list[str]]) -> list[str]:
    """Union user list with package defaults so `pip install -U` propagates
    new defaults without clobbering user additions (Config Option A). If the
    user list ends with an entry '__override__', their list is used verbatim
    (escape hatch for anyone who needs to intentionally remove a default).
    """
    if user_value is None:
        return defaults_fn()
    if not isinstance(user_value, list):
        return user_value  # pydantic will re-type-check and fail cleanly
    if "__override__" in user_value:
        return [v for v in user_value if v != "__override__"]
    defaults = defaults_fn()
    merged = list(defaults)
    for item in user_value:
        if item not in merged:
            merged.append(item)
    return merged


class BashToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    max_output_bytes: int = Field(
        default=16_000, description="Maximum bytes to capture from stdout and stderr."
    )
    default_timeout: int = Field(
        default=300, description="Default timeout for commands in seconds."
    )
    allowlist: list[str] = Field(
        default_factory=_get_default_allowlist,
        description=(
            "Command prefixes that are automatically allowed. User entries "
            "are UNIONED with package defaults (new defaults auto-propagate "
            "on pip install -U). Include '__override__' in the list to use "
            "your list verbatim and skip defaults."
        ),
    )
    denylist: list[str] = Field(
        default_factory=_get_default_denylist,
        description=(
            "Command prefixes that are automatically denied. User entries "
            "unioned with package defaults; see allowlist for override."
        ),
    )
    denylist_standalone: list[str] = Field(
        default_factory=_get_default_denylist_standalone,
        description="Commands that are denied only when run without arguments",
    )

    @field_validator("allowlist", mode="before")
    @classmethod
    def _merge_allowlist(cls, v: Any) -> list[str]:
        return _merge_with_defaults(v, _get_default_allowlist)

    @field_validator("denylist", mode="before")
    @classmethod
    def _merge_denylist(cls, v: Any) -> list[str]:
        return _merge_with_defaults(v, _get_default_denylist)

    @field_validator("denylist_standalone", mode="before")
    @classmethod
    def _merge_denylist_standalone(cls, v: Any) -> list[str]:
        return _merge_with_defaults(v, _get_default_denylist_standalone)


class BashArgs(BaseModel):
    command: str
    timeout: int | None = Field(
        default=None, description="Override the default command timeout."
    )


class BashResult(BaseModel):
    command: str
    stdout: str
    stderr: str
    returncode: int


class Bash(
    BaseTool[BashArgs, BashResult, BashToolConfig, BaseToolState],
    ToolUIData[BashArgs, BashResult],
):
    description: ClassVar[str] = "Run a one-off bash command and capture its output."

    @classmethod
    def format_call_display(cls, args: BashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"bash: {args.command}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, BashResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        return ToolResultDisplay(success=True, message=f"Ran {event.result.command}")

    @classmethod
    def get_status_text(cls) -> str:
        return "Running command"

    def resolve_permission(self, args: BashArgs) -> ToolPermission | None:
        if is_windows():
            return None

        command_parts = _extract_commands(args.command)
        if not command_parts:
            return None

        def is_denylisted(command: str) -> bool:
            return any(command.startswith(pattern) for pattern in self.config.denylist)

        def is_standalone_denylisted(command: str) -> bool:
            parts = command.split()
            if not parts:
                return False

            base_command = parts[0]
            has_args = len(parts) > 1

            if not has_args:
                command_name = os.path.basename(base_command)
                if command_name in self.config.denylist_standalone:
                    return True
                if base_command in self.config.denylist_standalone:
                    return True

            return False

        def is_allowlisted(command: str) -> bool:
            # Direct prefix match first ("find . -name x" vs "find").
            if any(command.startswith(pattern) for pattern in self.config.allowlist):
                return True
            # Also match on basename so "/usr/bin/find ..." or "./grep ..."
            # auto-approve alongside bare "find" / "grep". Without this,
            # the model invoking a fully-qualified path would drop to the
            # approval prompt unnecessarily (GitHub issue #6).
            parts = command.split(None, 1)
            if not parts:
                return False
            base = os.path.basename(parts[0])
            rest = f"{base} {parts[1]}" if len(parts) > 1 else base
            return any(rest.startswith(pattern) for pattern in self.config.allowlist)

        for part in command_parts:
            if is_denylisted(part):
                return ToolPermission.NEVER
            if is_standalone_denylisted(part):
                return ToolPermission.NEVER

        if all(is_allowlisted(part) for part in command_parts):
            return ToolPermission.ALWAYS

        return None

    @final
    def _build_timeout_error(self, command: str, timeout: int) -> ToolError:
        return ToolError(f"Command timed out after {timeout}s: {command!r}")

    @final
    def _build_result(
        self, *, command: str, stdout: str, stderr: str, returncode: int
    ) -> BashResult:
        if returncode != 0:
            # ADVISORY ONLY — never raise ToolError for non-zero exit codes.
            # Raising blocks the model: it gets <tool_error> with no useful
            # output, doesn't know if exit 1 means "no matches" (grep) or a
            # real failure, and retries identically.  Same pattern as the
            # timeout handler above.  Include a clear exit-code annotation so
            # the model can reason about what happened.
            # See feedback_no_tool_errors_for_loop_detection.md.
            annotation = f"[Exit code {returncode}]"
            import re as _rc_re
            _has_kill = _rc_re.search(r'\bkill\b', command)
            if returncode < 0:
                # Negative returncode means killed by a Unix signal.
                try:
                    sig_name = signal.Signals(-returncode).name
                except ValueError:
                    sig_name = f"signal {-returncode}"
                annotation += (
                    f" — process killed by {sig_name}."
                    " If this command starts a server or long-running process,"
                    " background it with `command &` and verify ports with `ss -tlnp`."
                )
            elif _has_kill and returncode in (1, 2):
                # `kill` on a non-existent PID returns exit 1 ("No such process")
                # or exit 2 (usage error when $! is unset in the subshell).
                # This is NOT a real failure — the server process already exited.
                # Give a targeted hint so the model doesn't retry the whole command.
                annotation += (
                    " — `kill` returned a non-zero exit code because the"
                    " target process was not found or $! was unset."
                    " This does NOT mean your server failed to start."
                    " If the server crash is real, its stderr output above"
                    " will show why. Do NOT re-run the same command —"
                    " read the server output and fix any crash there."
                )
            elif not stdout and not stderr:
                annotation += " (no output)"
                if returncode == 1:
                    annotation += (
                        " — for grep/find, exit code 1 means no matches found"
                    )
            annotated_stdout = (stdout + "\n" + annotation).strip() if stdout else annotation
            return BashResult(
                command=command,
                stdout=annotated_stdout,
                stderr=stderr,
                returncode=returncode,
            )

        # When grep emits "binary file X matches" to stderr with exit 0, the
        # model loops adding more | grep -v flags that don't help.  Annotate
        # stderr so it knows to add --include="*.py" instead.
        if returncode == 0 and stderr and "binary file" in stderr and "matches" in stderr:
            stderr = stderr + (
                "\n[Hint: grep skipped binary files (e.g. .pyc). "
                "Add --include='*.py' to restrict to Python source files.]"
            )

        return BashResult(
            command=command, stdout=stdout, stderr=stderr, returncode=returncode
        )

    async def run(
        self, args: BashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashResult, None]:
        timeout = args.timeout or self.config.default_timeout
        max_bytes = self.config.max_output_bytes

        proc = None
        try:
            # start_new_session is Unix-only, on Windows it's ignored
            kwargs: dict[Literal["start_new_session"], bool] = (
                {} if is_windows() else {"start_new_session": True}
            )

            proc = await asyncio.create_subprocess_shell(
                args.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=_get_base_env(),
                executable=_get_shell_executable(),
                **kwargs,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                await _kill_process_tree(proc)
                # ADVISORY ONLY — never raise ToolError on timeout; raising
                # causes the model to retry the exact same command (loop).
                # Track repeat timeouts per command and escalate the hint.
                # See feedback_no_tool_errors_for_loop_detection.md.
                timeout_state = self.state.__dict__.setdefault(
                    "_bash_timeout_history", {}
                )
                count = timeout_state.get(args.command, 0) + 1
                timeout_state[args.command] = count
                if count == 1:
                    hint = (
                        "The command was killed. If it starts a server or "
                        "long-running process, background it with `command &` "
                        "instead of running it in the foreground."
                    )
                else:
                    hint = (
                        f"[TIMEOUT #{count}] You have timed out {count}x on "
                        f"this exact command. Running it again will produce the "
                        f"same result. STOP retrying. Background servers with "
                        f"`&`, check existing listeners with `ss -tlnp`, or fix "
                        f"the underlying cause before re-running."
                    )
                yield BashResult(
                    command=args.command,
                    stdout=(
                        f"Command timed out after {timeout}s: {args.command!r}\n"
                        f"{hint}"
                    ),
                    stderr="",
                    returncode=124,
                )
                return

            encoding = _get_subprocess_encoding()
            stdout_raw = stdout_bytes.decode(encoding, errors="replace") if stdout_bytes else ""
            stderr_raw = stderr_bytes.decode(encoding, errors="replace") if stderr_bytes else ""
            if len(stdout_raw) > max_bytes:
                stdout = stdout_raw[:max_bytes] + (
                    f"\n[OUTPUT TRUNCATED: stdout exceeded {max_bytes} bytes. "
                    "Use 'grep -l' to list matching files only, narrow your "
                    "pattern, or pipe through 'head -50' to limit output. "
                    "Do NOT re-run the same command — the result will be "
                    "identical.]"
                )
            else:
                stdout = stdout_raw
            if len(stderr_raw) > max_bytes:
                stderr = stderr_raw[:max_bytes] + (
                    f"\n[STDERR TRUNCATED: exceeded {max_bytes} bytes]"
                )
            else:
                stderr = stderr_raw

            returncode = proc.returncode or 0

            # Proactive heredoc-write confirmation.  When `cat <<EOF > file`
            # succeeds silently (empty stdout, rc=0), the model can't tell the
            # file landed and re-runs the same heredoc.  Check the file on disk
            # and confirm immediately so the model moves on without a retry.
            import re as _re_hd
            import os as _os_hd
            _hd_match = _re_hd.search(
                r"cat\s+<<\s*['\"]?[A-Za-z_]*['\"]?\s+>+\s*(\S+)", args.command
            )
            if _hd_match and returncode == 0 and not stdout.strip():
                _hd_path = _hd_match.group(1).strip().rstrip(";")
                if _os_hd.path.exists(_hd_path):
                    _hd_size = _os_hd.path.getsize(_hd_path)
                    _hd_lines = 0
                    try:
                        with open(_hd_path, "r", errors="replace") as _hdf:
                            _hd_lines = sum(1 for _ in _hdf)
                    except Exception:
                        pass
                    stdout = (
                        f"[File written: {_hd_path} ({_hd_lines} lines, "
                        f"{_hd_size} bytes). "
                        f"The file is on disk — do not re-run this command.]"
                    )

            # Mechanical loop-breaker (ADVISORY ONLY — never raise
            # ToolError; see feedback_no_tool_errors_for_loop_detection.md).
            # Two complementary checks:
            #   A) Same command + byte-identical output: trigger on 3rd run.
            #   B) Same command + non-zero exit code, output varies: trigger
            #      on 5th failing run.  Covers "python3 -m pkg list" called
            #      14× where each traceback has slightly different content
            #      (timestamp, object id) that defeats the hash check.
            state = self.state.__dict__.setdefault("_bash_history", {})
            # Track total error-exit calls per command (regardless of output).
            err_state = self.state.__dict__.setdefault("_bash_err_count", {})
            # Track consecutive empty-stdout search commands (any command, not
            # just identical ones).  The model semantic-loops by trying different
            # search terms for a non-existent target:
            #   ls | grep "test_cli" → empty → ls | grep "test_race" → empty → ...
            # Each command is unique so the identical-hash check never fires.
            # After 5 consecutive empty searches reset is_search → nudge.
            _consec_empty_search_state = self.state.__dict__.setdefault(
                "_consec_empty_searches", {"count": 0}
            )
            if returncode != 0:
                err_count = err_state.get(args.command, 0) + 1
                err_state[args.command] = err_count
                if err_count >= 5:
                    cmd_preview = args.command[:80]
                    notice = (
                        f"[NOTICE: `{cmd_preview}` has failed with a non-zero "
                        f"exit code {err_count} times this session. "
                        f"Re-running without changing the code will produce the "
                        f"same error. STOP retrying. Read the full traceback "
                        f"above, identify the root cause, and fix the source "
                        f"file before running again. "
                        f"Latest stderr: {stderr[:200]}]"
                    )
                    yield self._build_result(
                        command=args.command,
                        stdout=notice,
                        stderr="",
                        returncode=returncode,
                    )
                    return
            combined = stdout + "\n---STDERR---\n" + stderr
            out_hash = hash((combined, returncode))
            entry = state.get(args.command)
            if entry and entry["hash"] == out_hash:
                entry["count"] += 1
                if entry["count"] >= 3:
                    cmd_preview = args.command[:80]
                    # Detect file-write-via-heredoc pattern: cat << ... > file
                    # The model writes a file, gets empty stdout (rc=0), then
                    # re-runs the same write thinking it didn't work.  Generic
                    # "EDIT SOURCE CODE" confuses it — give a targeted hint.
                    import re as _re
                    _is_heredoc_write = bool(_re.search(
                        r"cat\s+<<\s*['\"]?EOF['\"]?\s+>", args.command
                    ))
                    # Detect ls/grep/find search that returned empty output.
                    # The model runs `ls -F | grep "test_cli"` repeatedly when
                    # the file doesn't exist — each run returns empty stdout.
                    # Generic "EDIT SOURCE CODE" is confusing; tell it to
                    # stop searching and create the file instead.
                    # grep/rg return rc=1 when no matches found; ls returns 0.
                    # Accept both as "empty search" since the key signal is
                    # empty stdout on a repeated search command.
                    _is_empty_search = (
                        not stdout.strip()
                        and returncode in (0, 1)
                        and bool(_re.search(
                            r'(?:^|\|)\s*(?:ls\b|grep\b|find\b|rg\b)',
                            args.command,
                        ))
                    )
                    # Detect echo -e / printf with \n or \t escape sequences.
                    # These loops when the shell doesn't interpret the escapes
                    # (e.g. dash ignores echo -e; backslash doubling in quoting
                    # turns \t into literal \t instead of a tab).  The generic
                    # "EDIT SOURCE CODE" hint is wrong here — the model needs
                    # to use $'...' quoting or write_file instead.
                    _is_echo_escape = bool(_re.search(
                        r'(?:echo\s+.*-[eE]|printf)\b', args.command
                    ) and _re.search(r'\\[nt]', args.command))
                    # Detect sed -i with \n or \t in substitution patterns.
                    # GNU sed interprets \n in replacement strings but the shell
                    # may swallow the backslash before sed sees it (depends on
                    # quote style). The model loops retrying the same sed
                    # command when the substitution silently fails.
                    _is_sed_escape = bool(_re.search(
                        r"\bsed\b.*-i\b", args.command
                    ) and _re.search(r"\\[nt\\]", args.command))
                    if _is_empty_search:
                        notice = (
                            f"[NOTICE: this is the #{entry['count']}th identical "
                            f"run of `{cmd_preview}` — it returned empty output "
                            f"every time. The file, symbol, or pattern you are "
                            f"searching for does not exist yet. "
                            f"STOP searching — you will keep getting empty results. "
                            f"Either CREATE the missing file/function with "
                            f"write_file or search_replace, or ask the user what "
                            f"'{cmd_preview[:40]}' refers to. "
                            f"Do NOT re-run this search unchanged.]"
                        )
                    elif _is_heredoc_write:
                        notice = (
                            f"[NOTICE: this is the #{entry['count']}th identical "
                            f"heredoc write of `{cmd_preview}`. "
                            f"The file already has this exact content — re-running "
                            f"the same cat command will not change it. "
                            f"If the feature is still broken, READ the file you "
                            f"wrote (use read_file), find the bug in the content, "
                            f"then fix it with write_file or search_replace with "
                            f"CORRECTED content. Do NOT re-run this cat command.]"
                        )
                    elif _is_echo_escape:
                        notice = (
                            f"[NOTICE: this is the #{entry['count']}th identical "
                            f"run of `{cmd_preview}` — escape sequences (\\n, \\t) "
                            f"in echo -e / printf are NOT being interpreted correctly. "
                            f"This shell may be /bin/sh (dash) where echo -e is a "
                            f"no-op, or backslash doubling in quoting is consuming "
                            f"the escape. "
                            f"Use ANSI $'...' quoting: printf $'name\\\\tage\\\\n' "
                            f"OR use Python: python3 -c \"print('name\\\\tage')\" "
                            f"OR use write_file to create the test file directly. "
                            f"Do NOT re-run this command unchanged.]"
                        )
                    elif _is_sed_escape:
                        notice = (
                            f"[NOTICE: this is the #{entry['count']}th identical "
                            f"run of `{cmd_preview}` — the sed substitution with "
                            f"\\n / \\t escape sequences is a no-op. Shell quoting "
                            f"may have consumed the backslash before sed sees it, "
                            f"or the search pattern doesn't match any line. "
                            f"Use search_replace with a proper SEARCH/REPLACE block "
                            f"to make exact text edits, or use write_file to "
                            f"rewrite the file with the corrected content. "
                            f"Do NOT re-run this sed command unchanged.]"
                        )
                    else:
                        notice = (
                            f"[NOTICE: this is the #{entry['count']} identical "
                            f"run of `{cmd_preview}` with byte-identical "
                            f"output and rc={returncode}. Re-running will not "
                            f"change anything — you must EDIT SOURCE CODE to "
                            f"change this output. Previous stdout first 300 "
                            f"chars:\n{stdout[:300]}]"
                        )
                    yield self._build_result(
                        command=args.command,
                        stdout=notice,
                        stderr="",
                        returncode=returncode,
                    )
                    return
            else:
                state[args.command] = {"hash": out_hash, "count": 1}

            # Consecutive-empty-search cross-command check (C).  Tracks any
            # ls/grep/find/rg that returned empty stdout, regardless of command
            # text.  Resets on any search with non-empty output.  After 5
            # consecutive empty searches, injects a clarification nudge.
            import re as _re2
            _is_any_search = bool(_re2.search(
                r'(?:^|\|)\s*(?:ls\b|grep\b|find\b|rg\b)', args.command
            ))
            if _is_any_search:
                if not stdout.strip() and returncode in (0, 1):
                    _consec_empty_search_state["count"] += 1
                else:
                    _consec_empty_search_state["count"] = 0
            _consec = _consec_empty_search_state["count"]
            if _is_any_search and _consec >= 5 and not stdout.strip():
                cmd_preview2 = args.command[:80]
                stdout = (
                    f"[LOOP-BREAKER: {_consec} consecutive search commands have "
                    f"all returned empty results (most recent: `{cmd_preview2}`). "
                    f"The thing you are looking for does NOT exist in this project. "
                    f"STOP searching. Either (a) CREATE the missing file/function "
                    f"with write_file or search_replace, or (b) ask the user to "
                    f"clarify what they meant — e.g. 'I don't see any test for "
                    f"\"bug B\" — can you clarify what component you mean?'. "
                    f"Do NOT run another search command.]"
                )

            yield self._build_result(
                command=args.command,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            )

        except (ToolError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise ToolError(f"Error running command {args.command!r}: {exc}") from exc
        finally:
            if proc is not None:
                await _kill_process_tree(proc)
