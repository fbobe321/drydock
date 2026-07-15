"""Built-in tools for DryDock v3.

Tools: Read, Write, Edit, Bash, Glob, Grep
Each tool is a function (params, config) -> str.
"""
from __future__ import annotations

import os
import sys
import re
import difflib
import glob as _glob
import signal
import subprocess
import threading
import time
from pathlib import Path

# Hard ceiling on bytes read from a command's output. communicate() buffers ALL
# stdout in RAM before we ever truncate for context, so a runaway/infinite-output
# command (`yes`, `cat /dev/urandom`, a massive build log) could balloon memory
# to gigabytes within the timeout. We stream with a byte cap and kill the command
# once it's hit — bounding RAM regardless of how much it tries to produce.
_MAX_BASH_OUTPUT_BYTES = 256 * 1024  # 256 KB — plenty of context, safe for RAM
_PARTIAL_TAIL = 4000  # chars of pre-timeout output to keep (tail) in the timeout msg
_DEFAULT_TIMEOUT = 120
_MAX_TIMEOUT = 1800  # 30 min hard ceiling — a single command shouldn't hang longer


def _as_text(v, default: str = "") -> str:
    """Coerce a text-body arg (file content, replacement text) to a string. Local
    models sometimes send it as a JSON array of lines → newline-join; None →
    default; other scalars → str()."""
    if isinstance(v, (list, tuple)):
        return "\n".join(str(x) for x in v)
    if v is None:
        return default
    return v if isinstance(v, str) else str(v)


def _as_str_arg(v, default: str = "") -> str:
    """Coerce a scalar string arg (a path, a pattern) to a string. A single-value
    list is unwrapped (models sometimes wrap a lone path/pattern in an array)."""
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else default
    if v is None:
        return default
    return v if isinstance(v, str) else str(v)


def _coerce_int(value, default: int):
    """Coerce a model-supplied int arg (offset/limit) that may arrive as a string
    ("5") or junk. Falls back to default on anything non-numeric."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_timeout(value) -> int:
    """Make the model-supplied timeout robust: local models often send it as a
    string ("10"), and a 0/negative/absurd value would make EVERY command time
    out instantly (or hang for hours). Coerce to int, fall back to the default on
    junk, and clamp to (0, _MAX_TIMEOUT]."""
    try:
        t = int(float(value))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT
    if t <= 0:
        return _DEFAULT_TIMEOUT
    return min(t, _MAX_TIMEOUT)


def _detect_bash() -> str | None:
    """Absolute path to bash, or None to fall back to Popen's default /bin/sh.

    tool_bash runs commands under bash so the bash syntax the model naturally
    writes — [[ ]], <<< herestrings, arrays, {1..n} brace expansion, process
    substitution <(...), $'...' — actually works. On Debian/Ubuntu /bin/sh is
    dash, which silently rejects all of those ("Syntax error: ... unexpected"),
    a confusing failure the model then loops on."""
    import shutil
    found = shutil.which("bash")
    if found:
        return found
    # Common absolute locations, incl. Git-for-Windows and WSL bash on Windows.
    for p in ("/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash",
              r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe",
              r"C:\Windows\System32\bash.exe"):
        if os.path.exists(p):
            return p
    return None


def _is_windows_env(os_name: str, platform: str, environ) -> bool:
    """Treat as Windows if ANY signal says so — native CPython (os.name 'nt' /
    sys.platform 'win32') AND odd Pythons run from Git-Bash/MSYS/Cygwin (which
    report posix but where Windows always exports WINDIR/SystemRoot). WSL (real
    Linux) does not set WINDIR, so it correctly stays POSIX/bash."""
    return (
        os_name == "nt"
        or platform in ("win32", "cygwin", "msys")
        or bool(environ.get("WINDIR") or environ.get("SystemRoot"))
    )


_IS_WINDOWS = _is_windows_env(os.name, sys.platform, os.environ)


def _detect_shell() -> tuple[str, str]:
    """Pick the shell tool_bash runs commands in — returns (kind, path).

    Override with env DRYDOCK_SHELL=powershell|cmd|bash to force one. Otherwise:
    POSIX → bash (so the model's bash-isms work), else /bin/sh.
    Windows → PowerShell (what users actually launch drydock in — pwsh, else
    Windows PowerShell), else cmd.exe. WSL / Git-Bash is NEVER required on Windows.
    """
    import shutil

    def _powershell():
        p = shutil.which("pwsh") or shutil.which("powershell")
        return ("powershell", p) if p else None

    def _bash():
        b = _detect_bash()
        return ("bash", b) if b else ("sh", shutil.which("sh") or "/bin/sh")

    forced = os.environ.get("DRYDOCK_SHELL", "").strip().lower()
    if forced in ("powershell", "pwsh"):
        return _powershell() or ("cmd", shutil.which("cmd") or "cmd.exe")
    if forced == "cmd":
        return ("cmd", shutil.which("cmd") or "cmd.exe")
    if forced in ("bash", "sh"):
        return _bash()

    if _IS_WINDOWS:
        return _powershell() or ("cmd", shutil.which("cmd") or "cmd.exe")
    return _bash()


# Resolved once at import (in whatever environment drydock runs — Linux/macOS,
# the task container, or native Windows). _BASH_SHELL kept for back-compat.
_SHELL_KIND, _SHELL_PATH = _detect_shell()
_BASH_SHELL = _detect_bash()


def shell_display_name() -> str:
    """Human label for the shell the Bash tool actually runs commands in — so the
    TUI can show 'PowerShell'/'cmd' on Windows instead of the schema name 'Bash'."""
    return {"powershell": "PowerShell", "cmd": "cmd",
            "bash": "Bash", "sh": "sh"}.get(_SHELL_KIND, "Bash")


def tool_display_name(name: str) -> str:
    """Map a tool's schema name to its display label. The `Bash` tool (the name
    the model calls) is shown as the actual shell — so on Windows a user sees
    'PowerShell', not 'Bash'."""
    return shell_display_name() if name == "Bash" else name


# ANSI escape sequences (CSI colour/cursor, OSC title, and lone two-char escapes).
# Some tools emit these even to a pipe (--color=always, forced-colour test runners),
# and they're pure display control — noise that wastes the model's tokens.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


def _sanitize_bash_output(text: str) -> str:
    """Strip ANSI escape sequences and drop NUL bytes from command output. NUL is
    valid UTF-8 (so the errors='replace' decode keeps it) but raw NUL in a JSON
    tool-result trips some LLM servers' tokenizers; ANSI codes are display noise.
    Neither carries information the model needs. Newlines/tabs/Unicode are kept."""
    return _ANSI_RE.sub("", text).replace("\x00", "")


def _collapse_repeated_lines(text: str, run: int = 20) -> str:
    """Collapse a run of >= `run` IDENTICAL consecutive lines into one line + a
    count. Repetitive output (`yes`, a spinning progress log) tokenizes densely —
    32 KB of "y\\n" is ~24k tokens — so even after byte-capping it can eat a big
    slice of the context window. Collapsing makes it cheap without losing the
    signal. Non-repetitive output (a normal build log) is left untouched."""
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        j = i
        while j < n and lines[j] == lines[i]:
            j += 1
        count = j - i
        if count >= run:
            out.append(lines[i])
            out.append(f"[... {count - 1} more identical lines collapsed ...]")
        else:
            out.extend(lines[i:j])
        i = j
    return "\n".join(out)

from drydock.tool_registry import ToolDef, register
from drydock.guards import (
    conflict_marker_refusal,
    has_conflict_markers,
    python_syntax_warning,
    write_warnings,
)
from drydock import bash_safety


def _resolve_path(path: str, config: dict) -> str:
    """Resolve a relative path against the session working directory.

    File tools must agree with Bash on what the working directory is —
    otherwise Write creates a file Bash can't see. Relative paths are joined
    to config['cwd']; absolute paths pass through.
    """
    cwd = config.get("cwd")
    if cwd and not os.path.isabs(path):
        return os.path.join(cwd, path)
    return path

# ── Schemas ───────────────────────────────────────────────────────────────

SCHEMAS = [
    {
        "name": "Read",
        "description": "Read a file. Returns content with line numbers. Use limit/offset for large files. A very large file with no limit returns a STRUCTURE INDEX (key symbols + line numbers) instead of the whole file — then Read the ranges you need with offset/limit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to file"},
                "limit": {"type": "integer", "description": "Max lines to read"},
                "offset": {"type": "integer", "description": "Start line (0-indexed)"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "ViewImage",
        "description": "Look at an image file with your vision — use this when you need to SEE a screenshot, mockup, diagram, photo, or rendered output (.png/.jpg/.jpeg/.gif/.webp/.bmp). The image is shown to you so you can describe it, read text from it, or debug it. To READ TEXT OR DATA out of an image — a scanned document, invoice, receipt, form, or a screenshot with text — use ViewImage FIRST (you can read it directly); prefer it over OCR tools like tesseract/pdftotext, which are often unreliable or not installed. (Reading an image with the Read tool gives binary garbage; use ViewImage instead.)",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the image file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "Screenshot",
        "description": "CAPTURE the current screen and SEE it with your vision — use "
                       "when the user asks you to look at their screen, review what's "
                       "displayed, debug a GUI, or read something shown on screen. Takes "
                       "the screenshot and shows it to you directly (no need to call "
                       "ViewImage after). Works on Windows (PowerShell), macOS, and "
                       "Linux with a display. Optionally pass `path` to also save the PNG.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Optional file to save the PNG to. Omit to use a temp file."},
            },
        },
    },
    {
        "name": "Write",
        "description": "Write content to a file, creating parent directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": "Replace exact text in a file. old_string must match exactly. "
                       "By default old_string must be unique; pass replace_all:true "
                       "to replace every occurrence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string", "description": "Exact text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace ALL occurrences (default false)"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Bash",
        "description": "Execute a shell command. Returns stdout+stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds (default 120; bump to 1800 for builds/training/cracking)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Glob",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern e.g. **/*.py"},
                "path": {"type": "string", "description": "Base directory (default: cwd)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents with regex. Returns matching lines with file:line prefix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Directory or file to search"},
                "include": {"type": "string", "description": "File pattern e.g. *.py"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "todo",
        "description": (
            "Maintain a visible task checklist for a multi-step job. Pass the "
            "WHOLE list each call as plain text, one task per line, each "
            "prefixed with its status: '[ ]' to-do, '[~]' in progress (mark "
            "exactly ONE), '[x]' done. Calling it REPLACES the previous list. "
            "Plan up front, then call it again to flip a task to done and the "
            "next to in-progress as you go. Skip it for trivial single-step "
            "work, and don't call it twice with the same list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "string",
                    "description": "One task per line, each prefixed [ ] / [~] / [x].",
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "Consult",
        "description": (
            "Ask a SECOND, more powerful advisor model (if the user configured "
            "one, e.g. Gemini) for a second opinion — use when you're stuck, want "
            "to sanity-check a design/approach, or need an expert take on a tricky "
            "bug. It only advises (no tools); YOU act on the answer. Pass the "
            "relevant code/error in `context` so the advisor has what it needs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The specific question to ask the advisor."},
                "context": {"type": "string", "description": "Relevant code, error output, or background the advisor needs."},
            },
            "required": ["question"],
        },
    },
    {
        "name": "task",
        "description": (
            "Delegate a focused, READ-ONLY investigation to a sub-agent that "
            "runs in its OWN fresh context and can only Read/Glob/Grep/Bash. It "
            "explores, then returns a concise summary — keeping a big search out "
            "of your context. Use it for a self-contained question like 'where "
            "is auth handled?' or 'how does module X work?'. It CANNOT edit "
            "files, so act on its summary yourself. Give it one clear task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The investigation task, stated fully and self-contained.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional 3-5 word label for the task.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "Dispatch",
        "description": (
            "Run SEVERAL read-only investigation sub-agents at once (in "
            "parallel), each in its own fresh context with Read/Glob/Grep/Bash. "
            "Use it to answer multiple INDEPENDENT questions concurrently, e.g. "
            "'where is auth handled?', 'how does the DB layer work?', 'what tests "
            "exist?'. Returns each agent's summary. They cannot write or recurse."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "Up to 6 independent investigation tasks.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "The self-contained task."},
                            "label": {"type": "string", "description": "Optional short label."},
                        },
                        "required": ["prompt"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "Worker",
        "description": (
            "Delegate a self-contained CHUNK OF WORK to a sub-agent that runs in its "
            "OWN fresh context and CAN write — it has Read, Write, Edit, Bash, Glob, "
            "Grep, does the task end to end (creating/editing files, running commands, "
            "verifying), and returns only a short summary of what it changed. Use this "
            "to KEEP A BIG, SELF-CONTAINED SUBTASK OUT OF YOUR CONTEXT — e.g. 'implement "
            "and test the CSV parser in parser.py', 'add logging to every handler in "
            "api/'. Give it ONE clear task with enough detail to finish independently; "
            "it cannot ask you questions or spawn its own workers. Its file changes are "
            "real and shared with you. For read-only investigation use `task`/`Dispatch`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The self-contained task to complete (with enough detail to finish independently).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "GitStatus",
        "description": (
            "Show the current branch and a concise list of changed/staged/"
            "untracked files (git status). Use it to understand the working tree "
            "before editing or committing."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "GitDiff",
        "description": (
            "Show the diff of changes (git diff), with a --stat summary first and "
            "the body truncated to fit context. Set staged=true for staged "
            "changes; pass a path to scope it to one file/dir."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Optional file/dir to scope the diff."},
                "staged": {"type": "boolean", "description": "Diff staged changes instead of the working tree."},
            },
        },
    },
    {
        "name": "GitLog",
        "description": "Show the last n commits, one line each (git log --oneline).",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "How many commits (default 10)."},
            },
        },
    },
    {
        "name": "GitCommit",
        "description": (
            "Stage all changes and commit with a message (git add -A && git "
            "commit). Local and reversible; does NOT push. Use it to package a "
            "completed, verified change. Write a clear, specific message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The commit message."},
                "add_all": {"type": "boolean", "description": "Stage all changes first (default true)."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "StigRules",
        "description": (
            "List the rules in a DISA STIG checklist (.ckl / .cklb) with their "
            "status, optionally filtered by status (e.g. not_reviewed). Use it to "
            "see what needs assessing. Pair with StigRule to read one, StigSet to "
            "record a result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .ckl/.cklb file."},
                "status": {"type": "string", "description": "Optional filter: open|not_a_finding|not_applicable|not_reviewed"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "StigRule",
        "description": (
            "Read ONE STIG rule's full detail — Check Content + Fix Text — so you "
            "can assess it against system evidence. Assess one rule at a time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "rule_id": {"type": "string", "description": "Vuln_Num (V-…) or Rule_ID (SV-…)."},
            },
            "required": ["path", "rule_id"],
        },
    },
    {
        "name": "StigSet",
        "description": (
            "Record an assessment result for a STIG rule and save the checklist: "
            "set status (open / not_a_finding / not_applicable / not_reviewed) and "
            "the finding_details / comments narrative. Regenerates a valid "
            ".ckl/.cklb that re-imports into STIG Viewer / eMASS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "rule_id": {"type": "string"},
                "status": {"type": "string", "description": "open | not_a_finding | not_applicable | not_reviewed"},
                "finding_details": {"type": "string"},
                "comments": {"type": "string"},
            },
            "required": ["path", "rule_id"],
        },
    },
    {
        "name": "GraphQuery",
        "description": (
            "Query the RMF ontology GRAPH to TRACE relationships (typed: Control, "
            "Component, Vulnerability, Objective). Use it for traceability the text "
            "knowledge base can't give: a control's assessment objectives, which "
            "components implement a control, or which controls a component INHERITS "
            "from its parent system. ops: control <id>, family <id>, component "
            "<name>, implementers <control>, inherited <component>."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "description": "control | family | component | implementers | inherited"},
                "id": {"type": "string", "description": "control id, family id, or component name"},
            },
            "required": ["op", "id"],
        },
    },
    {
        "name": "GraphAdd",
        "description": (
            "Record a typed fact in the RMF ontology graph as you read an SSP / "
            "scan / checklist, so relationships can be traced later. ops: component "
            "(a system component), implements (component implements a control), "
            "resides_on (component resides on a parent/boundary — enables control "
            "inheritance), vulnerability (a finding affecting a component)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "description": "component | implements | resides_on | vulnerability | satisfies"},
                "component": {"type": "string"},
                "control": {"type": "string", "description": "control id, for op=implements/satisfies"},
                "parent": {"type": "string", "description": "parent component/boundary, for op=resides_on"},
                "rule": {"type": "string", "description": "STIG rule id, for op=satisfies"},
                "id": {"type": "string", "description": "vulnerability/STIG/CVE id, for op=vulnerability"},
                "severity": {"type": "string"},
                "os": {"type": "string"}, "ip": {"type": "string"}, "data_type": {"type": "string"},
            },
            "required": ["op"],
        },
    },
    {
        "name": "WebSearch",
        "description": (
            "Search the internet and get back the top results (title, URL, "
            "snippet). Use it for current events, docs, library/API details, "
            "error messages, or anything you're unsure about or that may have "
            "changed since training. Follow up with WebFetch to read a result in "
            "full. Returns a clean message if the machine is offline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "k": {"type": "integer", "description": "Max results (default 5)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "WebFetch",
        "description": (
            "Fetch a web page (or any URL) and return its readable text content, "
            "HTML stripped. Use it to read a page found via WebSearch or a URL the "
            "user gave you. Returns a clean message if the machine is offline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch."},
                "max_chars": {"type": "integer", "description": "Max chars to return (default 6000)."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "Knowledge",
        "description": (
            "Search the user's KNOWLEDGE BASE (a GraphRAG index they built from "
            "their own docs/code) for project-specific information you were not "
            "trained on. Returns the most relevant passages plus related entities "
            "from the graph. Use it BEFORE answering or coding when the task may "
            "depend on the user's private/project knowledge (their APIs, specs, "
            "data, conventions). If it returns no matches, the topic isn't in the "
            "base — answer normally."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look up, as a natural-language question or keywords.",
                },
                "k": {
                    "type": "integer",
                    "description": "Max passages to return (default 5).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "BuildKnowledge",
        "description": (
            "BUILD or extend the user's GraphRAG knowledge base FROM THEIR documents "
            "or code. Call this YOURSELF when the user asks you to index / ingest / "
            "'make a knowledge base from' a folder or file (e.g. 'create a knowledge "
            "base from my Documents'). Give `path` (a file or directory). mode 'build' "
            "= (re)build from scratch; 'add' = merge into an existing base. When it "
            "succeeds you can search it with the Knowledge tool. Do NOT tell the user "
            "to type a '/graphrag' slash command — actually do it with this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or directory to index. A normal path — on "
                                   "Windows e.g. C:\\Users\\me\\Documents (quotes optional).",
                },
                "mode": {
                    "type": "string",
                    "description": "'build' (rebuild from scratch, default) or 'add' "
                                   "(merge into the existing base).",
                },
            },
            "required": ["path"],
        },
    },
]

# ── Tool implementations ──────────────────────────────────────────────────

# Above this many lines, a Read with no explicit window returns a structural
# INDEX (key symbols + line numbers) instead of dumping the file — semantic
# chunking so a huge file can't blow the context window. The model then Reads
# the ranges it needs with offset/limit.
_BIG_FILE_LINES = 1500

# Structural anchors: a definition → a node the model can jump to. Code anchors
# apply to every file; markdown headers (anchored at column 0 so indented code
# COMMENTS aren't mistaken for headings) apply only to doc files.
_CODE_RE = re.compile(
    r"^\s*("
    r"(?:async\s+)?def\s+\w+"            # python functions
    r"|class\s+\w+"                       # python/js/etc classes
    r"|(?:export\s+)?(?:async\s+)?function\s+\w+"  # js/ts functions
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\("  # js arrow fns
    r"|func\s+\w+"                        # go
    r"|fn\s+\w+"                          # rust
    r")"
)
_MD_RE = re.compile(r"^#{1,6}\s+\S")  # markdown headers, column 0 only
_DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".org"}


def _file_index(lines: list[str], fp: str) -> str:
    """A compact, line-numbered index of a large file's structure."""
    is_doc = Path(fp).suffix.lower() in _DOC_EXT
    anchors = [
        f"  L{i + 1}: {ln.rstrip()[:100]}"
        for i, ln in enumerate(lines)
        if _CODE_RE.match(ln) or (is_doc and _MD_RE.match(ln))
    ]
    head = (
        f"{fp} is large ({len(lines)} lines) — showing a STRUCTURE INDEX instead "
        f"of the whole file (to save context). Read a specific section with "
        f"offset/limit (e.g. {{\"file_path\": ..., \"offset\": <line-1>, "
        f"\"limit\": 120}}).\n"
    )
    if not anchors:
        return head + "(no def/class/header anchors found — read ranges with offset/limit.)"
    body = "\n".join(anchors[:400])
    if len(anchors) > 400:
        body += f"\n  [... {len(anchors) - 400} more anchors ...]"
    return f"{head}\nKey locations ({len(anchors)}):\n{body}"


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_MAX_VIEW_IMAGE_BYTES = 20_000_000


def tool_viewimage(params: dict, config: dict) -> str:
    """Validate an image path and return a result that names it; the API boundary
    (providers.messages_to_openai) attaches the actual image to THIS tool result
    so the vision model sees it. Returns a plain error string on any problem
    (never an attachable path) so a bad call can't try to attach nothing."""
    raw = (params.get("path") or params.get("file_path") or "").strip()
    if not raw:
        return "Error: ViewImage needs a `path` to an image file."
    fp = _resolve_path(raw, config)
    if os.path.splitext(fp)[1].lower() not in _IMAGE_EXTS:
        return (f"Error: {raw} is not a supported image. ViewImage handles "
                f"{', '.join(_IMAGE_EXTS)}.")
    if not os.path.isfile(fp):
        return f"Error: no image file at {raw} (resolved to {fp})."
    size = os.path.getsize(fp)
    if size > _MAX_VIEW_IMAGE_BYTES:
        return f"Error: {raw} is {size // 1_000_000}MB — too large to view (limit 20MB)."
    kind = os.path.splitext(fp)[1].lstrip(".").upper()
    # The absolute path in this text is what the API boundary attaches.
    return (f"Loaded image {fp} ({kind}, {size // 1024 or 1}KB) — it is now visible "
            "to you. Describe it, read any text in it, or use it to answer the task.")


def _capture_screen(out: str) -> str | None:
    """Grab the whole (virtual) screen to PNG `out`. Returns an error string, or
    None on success. Per-OS: Windows uses PowerShell + System.Drawing; macOS uses
    screencapture; Linux tries the common grabbers in turn."""
    import shutil
    import subprocess
    try:
        if _IS_WINDOWS:
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                "$s=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
                "$b=New-Object System.Drawing.Bitmap $s.Width,$s.Height; "
                "$g=[System.Drawing.Graphics]::FromImage($b); "
                "$g.CopyFromScreen($s.Left,$s.Top,0,0,$b.Size); "
                f"$b.Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png); "
                "$g.Dispose(); $b.Dispose()"
            )
            exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=30)
        elif sys.platform == "darwin":
            subprocess.run(["screencapture", "-x", out], capture_output=True, timeout=30)
        else:  # Linux/BSD — try grabbers in order of ubiquity (X11 + wayland)
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                # Fail fast — a grabber on a headless box blocks until timeout.
                return ("no display ($DISPLAY/$WAYLAND_DISPLAY unset) — cannot capture "
                        "the screen on a headless server.")
            grabbers = (["scrot", "-o", out], ["import", "-window", "root", out],
                        ["gnome-screenshot", "-f", out], ["grim", out],
                        ["spectacle", "-b", "-n", "-o", out], ["maim", out])
            tried = [g[0] for g in grabbers if shutil.which(g[0])]
            if not tried:
                return ("no screenshot tool found. Install one of: scrot, imagemagick "
                        "(import), gnome-screenshot, grim (wayland), spectacle, maim.")
            for g in grabbers:
                if shutil.which(g[0]):
                    subprocess.run(g, capture_output=True, timeout=30)
                    if os.path.isfile(out) and os.path.getsize(out):
                        break
    except subprocess.TimeoutExpired:
        return "screen capture timed out (30s)."
    except Exception as e:  # noqa: BLE001 — report, never crash the loop
        return f"screen capture failed: {e}"
    return None


def tool_screenshot(params: dict, config: dict) -> str:
    """Capture the screen to a PNG and make it visible to the vision model — the
    returned absolute path is auto-attached by the API boundary, same as ViewImage."""
    import tempfile

    raw = _as_str_arg(params.get("path")).strip()
    out = os.path.abspath(_resolve_path(raw, config) if raw
                          else os.path.join(tempfile.gettempdir(), "drydock_screenshot.png"))
    try:
        if os.path.dirname(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
    except OSError:
        pass
    err = _capture_screen(out)
    if err:
        return (f"Error: {err} (On a headless server there is no display to capture.)")
    if not os.path.isfile(out) or not os.path.getsize(out):
        return "Error: screen capture produced no image (no display, or the grabber failed)."
    size = os.path.getsize(out)
    if size > _MAX_VIEW_IMAGE_BYTES:
        return (f"Captured the screen to {out} but it is {size // 1_000_000}MB — too "
                "large to view (limit 20MB). The file is saved; open it manually.")
    return (f"Captured the screen to {out} (PNG, {size // 1024 or 1}KB) — it is now "
            "visible to you. Describe what's on screen or use it to answer the task.")


def tool_read(params: dict, config: dict) -> str:
    fp = _resolve_path(_as_str_arg(params.get("file_path")), config)
    # Reading an image as text yields binary garbage — point at the vision tool.
    if os.path.splitext(fp)[1].lower() in _IMAGE_EXTS and os.path.isfile(fp):
        return (f"{params['file_path']} is an image — Read would return binary "
                "garbage. Use the ViewImage tool to actually SEE it.")
    limit = None if params.get("limit") is None else _coerce_int(params.get("limit"), 2000)
    offset = _coerce_int(params.get("offset", 0), 0)
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Huge file, no explicit window → index it instead of dumping it.
        if limit is None and offset == 0 and len(lines) > _BIG_FILE_LINES:
            return _file_index(lines, fp)
        eff_limit = 2000 if limit is None else limit
        selected = lines[offset:offset + eff_limit]
        numbered = [f"{i + offset + 1}\t{line.rstrip()}" for i, line in enumerate(selected)]
        # Drop NUL bytes (valid UTF-8, so errors='replace' keeps them) — raw NUL in
        # a JSON tool result trips some LLM servers. ANSI is left as-is: unlike
        # command output, a file's bytes are content the model may need verbatim.
        result = "\n".join(numbered).replace("\x00", "")
        if len(lines) > offset + eff_limit:
            result += f"\n[... {len(lines) - offset - eff_limit} more lines]"
        return result or "(empty file)"
    except FileNotFoundError:
        return f"Error: file not found: {fp}"
    except Exception as e:
        return f"Error reading {fp}: {e}"


def _record_undo(config: dict, fp: str, prev: str | None) -> None:
    """Push (path, prior-content) onto the session undo journal. prev=None
    means the file did not exist before, so undo deletes it."""
    config.setdefault("_undo", []).append((fp, prev))


def _snapshot(fp: str) -> str | None:
    """Prior content of fp, or None if it doesn't exist / can't be read."""
    p = Path(fp)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def undo_last(config: dict) -> str:
    """Revert the most recent Write/Edit. User-facing (called by /undo)."""
    journal = config.get("_undo") or []
    if not journal:
        return "Nothing to undo."
    fp, prev = journal.pop()
    try:
        if prev is None:
            Path(fp).unlink(missing_ok=True)
            return f"Undid write — removed {fp}"
        Path(fp).write_text(prev, encoding="utf-8")
        return f"Undid last change — restored {fp}"
    except OSError as e:
        return f"Could not undo {fp}: {e}"


def tool_write(params: dict, config: dict) -> str:
    # Truncated/invalid tool-call JSON arrives as {"_raw": ...} with no
    # file_path/content — usually a big file that overran the response. Say so
    # (and how to recover) instead of the misleading "needs a real file_path".
    if "_raw" in params and not params.get("file_path"):
        return (
            "Error: the Write arguments were cut off or not valid JSON, so "
            "nothing was written. This usually means the file was too large for "
            "one response — write it in smaller pieces (Write the first part, "
            "then Edit to append the rest)."
        )
    fp = params.get("file_path")
    # Empty/whitespace path is the canonical real-use loop bug — reject it
    # clearly (with the content length, so the model knows the write was seen)
    # instead of writing a file literally named " " or looping.
    if not fp or not str(fp).strip():
        return (
            "Error: Write needs a real file_path (the path was empty or blank). "
            "Pass the path you want to create, e.g. 'src/main.py'."
        )
    fp = _resolve_path(str(fp).strip(), config)
    if Path(fp).is_dir():
        return f"Error: {fp} is a directory, not a file. Pass a file path."
    # Local models sometimes send content as a JSON array of lines (or a number),
    # which f.write() can't take — coerce instead of crashing (tools must return
    # errors, never raise). A list → newline-joined lines.
    content = _as_text(params.get("content", ""))
    if has_conflict_markers(content):
        return conflict_marker_refusal(fp)
    prev = _snapshot(fp)
    try:
        Path(fp).parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        _record_undo(config, fp, prev)
        lines = content.count("\n") + 1
        result = f"Wrote {lines} lines to {fp}"
        # Advisory post-write checks (never block the write).
        warnings = write_warnings(fp, content)
        if warnings:
            result += "\n" + "\n".join(warnings)
        return result
    except Exception as e:
        return f"Error writing {fp}: {e}"


def _fuzzy_find(content: str, target: str) -> str | None:
    """Try to find target in content with whitespace normalization."""
    # Normalize whitespace: collapse multiple spaces/tabs, strip trailing
    def normalize(s):
        lines = s.split('\n')
        return '\n'.join(re.sub(r'[ \t]+', ' ', line.rstrip()) for line in lines)

    norm_content = normalize(content)
    norm_target = normalize(target)

    if norm_target in norm_content:
        # Find the original text that matches the normalized version
        # Search line by line
        target_lines = target.strip().split('\n')
        content_lines = content.split('\n')
        for i in range(len(content_lines) - len(target_lines) + 1):
            candidate = '\n'.join(content_lines[i:i + len(target_lines)])
            if normalize(candidate) == normalize(target.strip()):
                return candidate
    return None


def _closest_snippet(content: str, old: str, max_chars: int = 700) -> str | None:
    """The chunk of *content* most similar to *old*, so a failed Edit can show
    the model the REAL current text to copy instead of guessing again. Returns
    None if nothing is reasonably close (avoids misleading the model)."""
    target = old.strip()
    if not target:
        return None
    lines = content.split("\n")
    window = max(1, target.count("\n") + 1)
    best_ratio, best_i = 0.0, -1
    for i in range(max(1, len(lines) - window + 1)):
        cand = "\n".join(lines[i:i + window])
        r = difflib.SequenceMatcher(None, target, cand).ratio()
        if r > best_ratio:
            best_ratio, best_i = r, i
    if best_i >= 0 and best_ratio >= 0.5:
        return "\n".join(lines[best_i:best_i + window])[:max_chars]
    return None


def _fuzzy_apply_region(content: str, old: str, min_ratio: float = 0.90) -> str | None:
    """Find the UNIQUE region of *content* that *old* is a near-miss for, so the
    edit can be APPLIED there — a weak model often can't reproduce a long block
    verbatim. Conservative on purpose: only when the best match is high-ratio,
    clearly better than the runner-up, and appears exactly once (else None)."""
    target = old.strip()
    if not target or len(target) < 12:
        return None  # too short to fuzzy-apply safely
    lines = content.split("\n")
    window = max(1, target.count("\n") + 1)
    best_ratio, best_i, second = 0.0, -1, 0.0
    for i in range(max(1, len(lines) - window + 1)):
        cand = "\n".join(lines[i:i + window])
        r = difflib.SequenceMatcher(None, target, cand).ratio()
        if r > best_ratio:
            second, best_ratio, best_i = best_ratio, r, i
        elif r > second:
            second = r
    if best_i >= 0 and best_ratio >= min_ratio and (best_ratio - second) >= 0.04:
        region = "\n".join(lines[best_i:best_i + window])
        # Only apply when the FIRST-LINE INDENT matches: the model's new_string
        # is indented to ITS old_string, so replacing a region with a different
        # indent would shift the code and corrupt it. Require same leading ws.
        def _indent(s: str) -> str:
            first = next((ln for ln in s.split("\n") if ln.strip()), "")
            return first[: len(first) - len(first.lstrip())]
        if _indent(old) != _indent(region):
            return None
        if content.count(region) == 1:  # unique → safe to replace
            return region
    return None


def _infer_edit_target(directory: Path, old: str) -> Path | None:
    """If a directory was passed instead of a file, find the single file under
    it whose content contains old_string. Returns None if zero or many match
    (ambiguous), or if old is too short to match reliably. Ported from v2's
    directory→file inference, which stopped a 'is a directory' retry loop."""
    if not old or len(old.strip()) < 10:
        return None
    matches: list[Path] = []
    checked = 0
    for f in sorted(directory.rglob("*")):
        if checked > 200:
            break
        if not f.is_file() or "__pycache__" in f.parts or ".git" in f.parts:
            continue
        checked += 1
        try:
            if old in f.read_text(encoding="utf-8", errors="ignore"):
                matches.append(f)
        except OSError:
            continue
    return matches[0] if len(matches) == 1 else None


def tool_edit(params: dict, config: dict) -> str:
    fp = params.get("file_path")
    if not fp or not str(fp).strip():
        return "Error: Edit needs a real file_path (the path was empty or blank)."
    fp = _resolve_path(str(fp).strip(), config)
    # Coerce non-string old/new (a JSON array of lines, a number) instead of
    # crashing with a TypeError in the substring search / replace.
    old = _as_text(params.get("old_string", ""))
    new = _as_text(params.get("new_string", ""))
    if old == "":
        # An empty old_string matches between every char (count = len+1) → the
        # generic "found N times" is baffling. Say what's actually wrong.
        return ("Error: old_string is empty. Edit replaces existing text — give the "
                "exact text to find. To create a file or add to the end, use Write.")
    if has_conflict_markers(new):
        return conflict_marker_refusal(fp)
    # If a directory was passed, try to infer the intended file (avoids a
    # 'is a directory' retry loop). Only when exactly one file matches old.
    if Path(fp).is_dir():
        inferred = _infer_edit_target(Path(fp), old)
        if inferred is None:
            return (
                f"Error: {fp} is a directory, not a file. Pass the exact file "
                f"path. (Could not unambiguously infer which file you meant.)"
            )
        fp = str(inferred)
    try:
        content = Path(fp).read_text(encoding="utf-8")
        if old not in content:
            # Fallback 1: already-applied — the replacement is already present
            # and the original is gone. Treat as a no-op success, not an error,
            # so the model doesn't retry in a loop.
            if new and new in content:
                return (
                    f"No change: {fp} already contains the new text "
                    f"(this edit appears to have been applied already)."
                )
            # Fallback 2: fuzzy match on whitespace, then a high-confidence
            # unique near-match (so a weak model's not-quite-verbatim block still
            # applies instead of looping).
            fuzzy = _fuzzy_find(content, old) or _fuzzy_apply_region(content, old)
            if fuzzy:
                old = fuzzy
            else:
                # Show the closest real text so the next edit is a copy, not a
                # re-guess — this is what breaks the same-file edit-thrash loop.
                snippet = _closest_snippet(content, old)
                if snippet:
                    return (
                        f"Error: old_string not found in {fp}. The closest text "
                        f"actually in the file is:\n---\n{snippet}\n---\n"
                        f"Copy that EXACTLY (including indentation) as old_string, "
                        f"or use Read to see the full current file."
                    )
                return (
                    f"Error: old_string not found in {fp}. Read the file first "
                    f"to copy the exact text (including indentation)."
                )
        count = content.count(old)
        # replace_all (models expect it, like the standard Edit tool): replace
        # EVERY occurrence. Without it, multiple matches are ambiguous → error.
        replace_all = bool(params.get("replace_all", False))
        if count > 1 and not replace_all:
            return (
                f"Error: old_string found {count} times in {fp}. Add more context "
                f"to make it unique, or pass replace_all: true to replace all."
            )
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        if updated == content:
            # No-op edit (old_string == new_string, or the replacement changes
            # nothing). Reporting "Edited" here is a false success that invites
            # the model to re-issue the identical call forever. Tell it plainly
            # that nothing changed and to move on — this is what breaks the
            # same-file edit-thrash loop at its source.
            return (
                f"No change: that edit leaves {fp} byte-for-byte identical "
                f"(old_string and new_string are effectively the same). Nothing "
                f"was written. Do NOT repeat this edit — the file already has "
                f"this content, so move on to your next step."
            )
        Path(fp).write_text(updated, encoding="utf-8")
        _record_undo(config, fp, content)
        result = f"Edited {fp}: replaced {len(old)} chars with {len(new)} chars"
        if replace_all and count > 1:
            result += f" ({count} occurrences)"
        # Advisory post-edit syntax check.
        warn = python_syntax_warning(fp, updated)
        if warn:
            result += "\n" + warn
        return result
    except FileNotFoundError:
        return f"Error: file not found: {fp}. Use Write to create it."
    except Exception as e:
        return f"Error editing {fp}: {e}"


def kill_process_group(proc) -> None:
    """Kill a Popen launched with start_new_session AND all its descendants by
    signalling the whole process group. proc.kill() alone only kills the direct
    shell, orphaning children (which keep running and hold the stdout pipe open,
    hanging communicate() and freezing the TUI on STOP). Falls back to proc.kill()
    if the group can't be resolved (e.g. process already gone)."""
    if proc is None:
        return
    if _IS_WINDOWS:
        # No process groups / SIGKILL on Windows — taskkill /T kills the whole
        # child tree (the shell + everything it spawned).
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


_OFFLINE_HINT = (
    "\n[Note: this looks like a network/download command and the environment "
    "appears to be OFFLINE — downloads will keep failing no matter how many "
    "times you retry. Do NOT repeat the fetch. Use only files/data already "
    "present in the project; if the required data genuinely isn't here, say so "
    "and stop rather than looping on the download.]"
)
# Commands that reach the network (so an offline failure is expected, not a bug).
_NETWORK_CMD_RE = re.compile(
    r"\b(pip3?\s+install|pip\s+download|uv\s+(pip\s+)?(install|add)|conda\s+install|"
    r"npm\s+(install|i|ci)|yarn\s+add|wget|curl\s+[^|]*https?://|git\s+clone|"
    r"apt(-get)?\s+(install|update)|hf\s+download|huggingface-cli\s+download|"
    r"load_dataset|hf_hub_download|snapshot_download|from_pretrained)\b",
    re.IGNORECASE,
)
# Output signatures of a name-resolution / connectivity failure.
_NETWORK_FAIL_RE = re.compile(
    r"(could not resolve|name or service not known|temporary failure in name "
    r"resolution|network is unreachable|no route to host|connection (timed out|"
    r"refused)|failed to establish a new connection|max retries exceeded|"
    r"getaddrinfo|no address associated|could not find a version|offline mode "
    r"is enabled|connectionerror|read timed out)",
    re.IGNORECASE,
)


def _is_network_command(cmd: str) -> bool:
    return bool(_NETWORK_CMD_RE.search(cmd or ""))


def _looks_like_network_failure(output: str) -> bool:
    return bool(_NETWORK_FAIL_RE.search(output or ""))


def tool_bash(params: dict, config: dict) -> str:
    cmd = params.get("command")
    if not cmd:
        if "_raw" in params:
            return (
                "Error: your tool arguments were not valid JSON so the command "
                "could not be read. Resend as ONE JSON object "
                '{"command": "..."} with the whole command as a single string '
                "(escape any newline as \\n; avoid raw line breaks in the JSON)."
            )
        return "Error: Bash needs a non-empty 'command'."
    # Default 120s (was 30s): real coding tasks compile, train, run test suites,
    # and crack hashes — a 30s wall killed legitimate long work before it could
    # finish (terminal-bench: 16 tasks died at 30s on builds/training). Bounded,
    # and the model can pass a larger `timeout` for genuinely heavy commands.
    timeout = _coerce_timeout(params.get("timeout", 120))
    reason = bash_safety.dangerous_command(cmd)
    if reason is not None:
        return bash_safety.refusal_message(cmd, reason)
    # Approval tier: sensitive-but-legitimate commands (sudo, installs, network)
    # run only after the user okays them. config["request_approval"] is the
    # UI callback; absent it (headless/tests) the command runs.
    approval_reason = bash_safety.requires_approval(cmd)
    if approval_reason and not config.get("_approve_all"):
        approver = config.get("request_approval")
        if approver is not None:
            decision = approver(cmd, approval_reason)
            if decision == "always":
                config["_approve_all"] = True
            elif decision != "allow":
                return (
                    f"REFUSED: you declined to approve this command "
                    f"({approval_reason}).\nCommand: {cmd.strip()}"
                )
    # Run via Popen (not subprocess.run) so STOP can kill it mid-execution:
    # the handle is stashed in config["_abort"]["proc"] for action_stop to kill.
    # start_new_session=True puts the shell in its OWN process group so we can
    # kill the WHOLE tree (kill_process_group): a bare proc.kill() only kills the
    # /bin/sh shell, orphaning its children (e.g. a brute-force script and its
    # subprocesses) which keep running AND keep the stdout pipe open — so the
    # follow-up communicate() blocks forever and the TUI is stuck "working".
    # We poll communicate() in short slices, checking the cancel Event and the
    # overall timeout between slices.
    cancel = config.get("_cancel")
    proc = None
    # Invoke bash explicitly as [bash, "-c", cmd] rather than shell=True +
    # executable — cross-platform correctness: on Windows shell=True builds
    # `{executable} /c {cmd}` (cmd.exe syntax), so a real bash.exe (Git Bash/WSL)
    # would be called as `bash /c ...` and fail. `[bash, "-c", cmd]` runs the same
    # on Linux and Windows. Without bash, fall back to the platform default shell
    # (cmd.exe on Windows, /bin/sh on POSIX) via shell=True.
    # Build the invocation for the detected shell. Explicit argv (shell=False)
    # for bash/PowerShell so it's correct on every OS; shell=True lets the
    # platform default (/bin/sh on POSIX, cmd.exe on Windows) handle the rest.
    if _SHELL_KIND == "bash":
        popen_cmd, use_shell = [_SHELL_PATH, "-c", cmd], False
    elif _SHELL_KIND == "powershell":
        popen_cmd, use_shell = [_SHELL_PATH, "-NoProfile", "-NonInteractive",
                                "-Command", cmd], False
    else:  # "sh" (POSIX) or "cmd" (Windows) → platform default shell
        popen_cmd, use_shell = cmd, True
    try:
        proc = subprocess.Popen(
            popen_cmd, shell=use_shell,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # stdin=DEVNULL so a command that reads stdin gets immediate EOF
            # (correct for a non-interactive tool) instead of inheriting the
            # TUI's terminal — where it would steal the user's keystrokes or hang
            # waiting for input Textual has captured. The agent still feeds input
            # explicitly via a pipe/redirect (echo x | cmd, cmd < file), which
            # overrides this.
            stdin=subprocess.DEVNULL,
            # errors="replace" so BINARY / non-UTF8 output (cat a binary, a tool
            # emitting raw bytes, gzip to stdout) decodes to replacement chars
            # instead of raising UnicodeDecodeError inside the reader thread —
            # which killed the thread and handed the agent "(no output)", losing
            # even the text parts of mixed output.
            text=True, encoding="utf-8", errors="replace",
            cwd=config.get("cwd"),
            # Own process group / job so kill_process_group takes down the whole
            # tree: setsid on POSIX; a new process group on Windows (getattr keeps
            # the Windows-only flag out of the POSIX code path — it resolves to 0).
            start_new_session=not _IS_WINDOWS,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        config.setdefault("_abort", {})["proc"] = proc
        # Read output in a daemon thread with a HARD byte cap, so memory can't
        # balloon on a runaway-output command. The thread stops (and we kill the
        # process) once the cap is hit; the main loop polls cancel + timeout.
        chunks: list[str] = []
        total = [0]
        capped = threading.Event()

        def _drain():
            assert proc.stdout is not None
            while True:
                block = proc.stdout.read(8192)
                if not block:
                    break
                chunks.append(block)
                total[0] += len(block)
                if total[0] >= _MAX_BASH_OUTPUT_BYTES:
                    capped.set()
                    break

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()
        start = time.monotonic()
        backgrounded = False
        while reader.is_alive():
            reader.join(0.3)
            if capped.is_set():
                kill_process_group(proc)  # stop it producing more
                break
            if cancel is not None and cancel.is_set():
                kill_process_group(proc)
                proc.wait()
                # The drain thread blocks in read(8192) until the buffer fills or
                # EOF; killing the process delivers EOF, so join lets it append the
                # last buffered output BEFORE we read chunks (else partial races
                # the drain and comes back empty for small-output commands).
                reader.join(1.0)
                partial = _sanitize_bash_output(
                    _collapse_repeated_lines("".join(list(chunks)))
                ).rstrip()
                if partial:
                    tail = partial[-_PARTIAL_TAIL:]
                    if len(partial) > _PARTIAL_TAIL:
                        tail = "[... earlier output truncated ...]\n" + tail
                    return f"[stopped by user]\n\n--- output before stop ---\n{tail}"
                return "[stopped by user]"
            # The SHELL has exited but the pipe is still open → the command
            # backgrounded a child (`cmd &`, a server the task wants to keep
            # running) that inherited stdout. Don't wait for it (that would hang
            # until the timeout) and DON'T kill it — return what we have so the
            # background process survives. (Redirecting its output, `cmd >log &`,
            # closes the pipe and never reaches here.)
            if proc.poll() is not None:
                reader.join(0.5)  # brief grace for any final buffered output
                if reader.is_alive():
                    backgrounded = True
                    break
            if time.monotonic() - start > timeout:
                kill_process_group(proc)
                proc.wait()
                reader.join(1.0)  # flush buffered output before building partial
                bigger = min(timeout * 4, 1800)
                msg = (
                    f"Error: command timed out after {timeout}s. If it is "
                    f"legitimately slow (a big query, build, download, or "
                    f"test run), retry with a larger timeout — pass "
                    f"timeout: {bigger}. Otherwise it may be hung."
                )
                if _is_network_command(cmd):
                    msg += _OFFLINE_HINT
                # Preserve any output produced BEFORE the hang — a test run or
                # build that prints results/diagnostics then stalls would
                # otherwise lose exactly what the agent needs. Tail-bounded so a
                # big partial can't bloat context.
                partial = _sanitize_bash_output(
                    _collapse_repeated_lines("".join(list(chunks)))
                ).rstrip()
                if partial:
                    tail = partial[-_PARTIAL_TAIL:]
                    if len(partial) > _PARTIAL_TAIL:
                        tail = "[... earlier output truncated ...]\n" + tail
                    msg += f"\n\n--- output before timeout ---\n{tail}"
                return msg
        if not backgrounded:
            proc.wait()
        # Collapse repetitive runs FIRST (turns 256 KB of "y\n" into ~2 lines),
        # then note if we hit the byte cap. Bounds both RAM (the cap) and context
        # tokens (the collapse). Snapshot chunks (list()) in case the reader
        # daemon is still appending for a backgrounded child.
        output = _sanitize_bash_output(_collapse_repeated_lines("".join(list(chunks))))
        if backgrounded:
            return (output.rstrip() + "\n[a process was left running in the background; "
                    "the command returned. Check it with a follow-up command.]").lstrip("\n")
        if capped.is_set():
            return (
                output.rstrip()
                + f"\n[output truncated at {_MAX_BASH_OUTPUT_BYTES // 1024} KB — "
                "the command produced more; redirect to a file and inspect it in "
                "pieces (head/tail/grep) instead of dumping it all]"
            )
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
            # Offline environments make downloads fail forever; the model tends
            # to retry the same fetch in a loop. Tell it to stop and work local.
            if _is_network_command(cmd) and _looks_like_network_failure(output):
                output += _OFFLINE_HINT
        return output.strip() or "(no output)"
    except Exception as e:
        return f"Error: {e}"
    finally:
        config.get("_abort", {}).pop("proc", None)


def tool_glob(params: dict, config: dict) -> str:
    pattern = _as_str_arg(params.get("pattern"))
    if not pattern:
        return "Error: Glob needs a 'pattern' (e.g. '**/*.py')."
    base = _as_str_arg(params.get("path")) or config.get("cwd") or "."
    try:
        matches = sorted(_glob.glob(os.path.join(base, pattern), recursive=True))
        if not matches:
            return "(no matches)"
        if len(matches) > 200:
            return "\n".join(matches[:200]) + f"\n[... {len(matches) - 200} more]"
        return "\n".join(matches)
    except Exception as e:
        return f"Error: {e}"


def tool_grep(params: dict, config: dict) -> str:
    pattern = _as_str_arg(params.get("pattern"))
    path = _as_str_arg(params.get("path")) or config.get("cwd") or "."
    include = _as_str_arg(params.get("include", ""))
    try:
        cmd = ["grep", "-rn", "--color=never"]
        if include:
            cmd.extend(["--include", include])
        cmd.extend([pattern, path])
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        # grep exit codes: 0 = matched, 1 = no match, >=2 = ERROR (invalid regex,
        # unreadable path). Don't report an error as "(no matches)" — the model
        # would wrongly conclude the pattern is absent instead of fixing it.
        if result.returncode >= 2:
            err = (result.stderr or "").strip() or "grep failed"
            return f"Error: {err.splitlines()[0]}"
        output = _sanitize_bash_output(result.stdout).strip()
        if not output:
            return "(no matches)"
        lines = output.split("\n")
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n[... {len(lines) - 100} more matches]"
        return output
    except subprocess.TimeoutExpired:
        return "Error: grep timed out"
    except Exception as e:
        return f"Error: {e}"


_TODO_DONE = {"x", "X", "✓"}
_TODO_DOING = {"~", "-", "/", "*"}


def parse_todo(tasks) -> list[tuple[str, str]]:
    """Parse a checklist into (text, status) pairs. Each non-empty line is one
    task, optionally prefixed with [x] done / [~] in_progress / [ ] pending.
    Deliberately forgiving: a bare line counts as pending, and `tasks` may be a
    newline-joined string OR a list of lines (the model sometimes sends a JSON
    array instead of one string — handle both rather than crash the TUI)."""
    if isinstance(tasks, (list, tuple)):
        tasks = "\n".join(str(t) for t in tasks)
    elif tasks is not None and not isinstance(tasks, str):
        tasks = str(tasks)
    out: list[tuple[str, str]] = []
    for raw in (tasks or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if not line:
            continue
        status = "pending"
        m = re.match(r"^\[\s*([^\]]?)\s*\]\s*(.*)$", line)
        if m:
            mark, line = m.group(1), m.group(2).strip()
            if mark in _TODO_DONE:
                status = "done"
            elif mark in _TODO_DOING:
                status = "in_progress"
        if line:
            out.append((line, status))
    return out


def tool_todo(params: dict, config: dict) -> str:
    """Maintain the visible task checklist (replace semantics)."""
    items = parse_todo(params.get("tasks", ""))
    if not items:
        return "Error: no tasks parsed. Pass one task per line, e.g. '[ ] do X'."
    config["_todo"] = items  # the TUI reads this to render the panel
    done = sum(1 for _, s in items if s == "done")
    doing = sum(1 for _, s in items if s == "in_progress")
    return f"Plan updated: {len(items)} tasks ({done} done, {doing} in progress)."


# ── Read-only sub-agent (the `task` tool) ─────────────────────────────────

# Tools a sub-agent may use. Excludes Write/Edit/todo (no silent mutations the
# parent can't see) and `task` itself (no recursion). Bash is included for real
# exploration (ls/cat/grep/find/git log) and is bounded by the same bash_safety.
SUBAGENT_TOOLS = ("Read", "ViewImage", "Glob", "Grep", "Bash")

_SUBAGENT_SYSTEM = (
    "You are a focused exploration sub-agent inside Drydock. You have read-only "
    "tools: Read, Glob, Grep, and Bash (use Bash only to INSPECT — ls, cat, "
    "grep, find, git log — never to modify files). Investigate the task you are "
    "given, then STOP and reply with a concise, factual summary of what you "
    "found: concrete file:line references and only the few key code snippets that "
    "matter, not narration and not whole files. Aim for under ~250 words — the "
    "main agent only gets this summary (not your tool output), so distill, don't "
    "dump. Do NOT try to edit or create files — the main agent acts on your findings."
)

# A WRITABLE worker sub-agent: it can actually DO the work (Write/Edit/Bash), in
# its own fresh context, and reports back only a summary. No recursion tools, so
# it can't spawn its own sub-agents.
WORKER_TOOLS = ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "ViewImage")

_WORKER_SYSTEM = (
    "You are a focused WORKER sub-agent inside Drydock, running in your own fresh "
    "context. You have full working tools — Read, Write, Edit, Bash, Glob, Grep — "
    "so you can actually DO the task: create and edit files, run commands, and "
    "verify your work. Complete the ONE task you are given, end to end. If it ships "
    "a test or check, RUN it and fix failures until it passes. When done, STOP and "
    "reply with a concise summary of WHAT YOU CHANGED — the files you created/edited "
    "and the outcome (e.g. 'tests pass'). Aim for under ~200 words; the main agent "
    "only receives this summary, not your tool output, so report results, not "
    "narration. Do not ask the main agent questions — just do the work."
)


def tool_consult(params: dict, config: dict) -> str:
    """Ask the configured second/advisor model (e.g. Gemini) for a second opinion."""
    from drydock import advisor

    question = _as_str_arg(params.get("question") or params.get("prompt")).strip()
    if not question:
        return "Error: Consult needs a `question`."
    return advisor.consult(question, config, context=params.get("context", "") or "")


# A sub-agent's whole job is to keep its investigation OUT of the main agent's
# context and hand back only a partition. The system prompt asks for a concise
# summary, but a runaway model could still return a wall of text — so cap what
# crosses back into the parent's window. ~4000 chars ≈ ~1000 tokens.
_SUBAGENT_SUMMARY_CAP = 4000


def _cap_summary(text: str) -> str:
    if len(text) <= _SUBAGENT_SUMMARY_CAP:
        return text
    head = text[:_SUBAGENT_SUMMARY_CAP].rsplit("\n", 1)[0] or text[:_SUBAGENT_SUMMARY_CAP]
    dropped = len(text) - len(head)
    return (head + f"\n[… sub-agent summary truncated, {dropped} chars dropped to keep it out "
            "of the main context. Ask a narrower follow-up sub-agent task if you need more.]")


def _run_subagent(prompt: str, config: dict, *, tools=SUBAGENT_TOOLS,
                  system: str | None = None, max_turns: int = 24,
                  max_tool_calls: int = 20, read_only: bool = True) -> str:
    """Run one sub-agent to completion in a FRESH context and return its final
    summary. Shared by `task`/`Dispatch` (read-only) and `Worker` (can write).
    Hard-capped; never raises (a sub-agent must not crash the parent turn). The
    summary is size-capped (_cap_summary) so a sub-agent can never bloat the main
    context — its tool output stays entirely in the sub-agent's own context.

    The run is described by a WorkerSpec (PRD Epic R), which STRUCTURALLY enforces
    read-only: a read-only worker has every mutating tool stripped, so it cannot
    edit files even if `tools` mistakenly included one."""
    from drydock.agent import run as agent_run, AgentState, TurnDone
    from drydock.subagents import WorkerSpec

    spec = WorkerSpec(
        objective=prompt, allowed_tools=list(tools), read_only=read_only,
        system_prompt=system or _SUBAGENT_SYSTEM, max_turns=max_turns,
        max_tool_calls=max_tool_calls, summary_cap=_SUBAGENT_SUMMARY_CAP,
    )
    sub_state = AgentState()
    sub_config = dict(config)
    sub_config["tool_allowlist"] = spec.enforced_tools()
    sub_config["force_first_tool"] = False
    sub_config["max_turns"] = spec.max_turns       # bound the helper hard
    sub_config["max_tool_calls"] = spec.max_tool_calls
    sub_config.pop("_todo", None)      # the sub-agent keeps no checklist of its own
    sub_config.pop("_plan_autocontinue", None)
    sub_config["trajectory_file"] = ""  # a sub-agent never overwrites the parent's trajectory
    _sys = spec.system_prompt
    # Own abort holder so parallel sub-agents don't clobber each other's (or the
    # parent's) in-flight client/proc handles in the shared dict.
    sub_config["_abort"] = {}
    steps = 0
    try:
        for ev in agent_run(prompt, sub_state, sub_config, _sys):
            if isinstance(ev, TurnDone):
                steps += 1
    except Exception as e:  # a sub-agent must never crash the parent turn
        return f"[sub-agent error: {e}]"
    for msg in reversed(sub_state.messages):
        if msg.get("role") == "assistant" and (msg.get("content") or "").strip():
            return _cap_summary(msg["content"].strip())
    return f"[sub-agent finished {steps} step(s) with no summary]"


def tool_task(params: dict, config: dict) -> str:
    """Spawn a read-only sub-agent with a FRESH context for a focused
    exploration task, returning only its final summary. Keeps big searches out
    of the main agent's context. Cannot recurse (no `task` tool) and cannot
    write (no Write/Edit), so it can never corrupt the parent's work."""
    prompt = (params.get("prompt") or params.get("description") or "").strip()
    if not prompt:
        return "Error: `task` needs a `prompt` describing what to investigate."
    return _run_subagent(prompt, config)


def tool_dispatch(params: dict, config: dict) -> str:
    """Run SEVERAL read-only sub-agents concurrently and return all summaries.
    Each gets its own fresh context + Read/Glob/Grep/Bash (no recursion, no
    writes). Use it to investigate independent questions at once."""
    import concurrent.futures

    raw = params.get("tasks") or params.get("agents") or []
    if isinstance(raw, dict):
        raw = [raw]
    norm: list[dict] = []
    for t in raw:
        if isinstance(t, str) and t.strip():
            norm.append({"prompt": t.strip(), "label": ""})
        elif isinstance(t, dict):
            p = (t.get("prompt") or t.get("description") or "").strip()
            if p:
                norm.append({"prompt": p, "label": (t.get("label") or t.get("description") or "").strip()})
    if not norm:
        return "Error: `Dispatch` needs a `tasks` list, each item a prompt (string) or {prompt, label}."
    norm = norm[:6]  # cap fan-out

    results: list[str] = [""] * len(norm)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(norm))) as ex:
        futs = {ex.submit(_run_subagent, t["prompt"], config): i for i, t in enumerate(norm)}
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:  # noqa: BLE001
                results[i] = f"[agent error: {e}]"
    parts = [f"Dispatched {len(norm)} sub-agent(s) in parallel:"]
    for i, t in enumerate(norm):
        label = t["label"] or f"agent {i + 1}"
        parts.append(f"\n=== {label} ===\n{results[i]}")
    return "\n".join(parts)


def tool_worker(params: dict, config: dict) -> str:
    """Delegate a self-contained CHUNK OF WORK to a WRITABLE sub-agent that runs in
    its own fresh context (can Read/Write/Edit/Bash), does the task end-to-end, and
    returns only a summary — so the work stays out of the main context window."""
    prompt = (params.get("prompt") or params.get("task") or params.get("description") or "").strip()
    if not prompt:
        return ("Error: `Worker` needs a `prompt` — a clear, self-contained task to do "
                "(e.g. 'implement parse_config() in config.py and make its unit test pass').")
    return _run_subagent(prompt, config, tools=WORKER_TOOLS, system=_WORKER_SYSTEM,
                         max_turns=40, max_tool_calls=40, read_only=False)


def _git_cwd(config: dict) -> str:
    return config.get("cwd") or os.getcwd()


def tool_gitstatus(params: dict, config: dict) -> str:
    from drydock import gittools
    try:
        return gittools.status(_git_cwd(config))
    except gittools.GitError as e:
        return f"git status failed: {e}"


def tool_gitdiff(params: dict, config: dict) -> str:
    from drydock import gittools
    try:
        return gittools.diff(
            _git_cwd(config),
            path=(params.get("path") or None),
            staged=bool(params.get("staged")),
        )
    except gittools.GitError as e:
        return f"git diff failed: {e}"


def tool_gitlog(params: dict, config: dict) -> str:
    from drydock import gittools
    try:
        n = int(params.get("n") or 10)
    except (TypeError, ValueError):
        n = 10
    try:
        return gittools.log(_git_cwd(config), n=n)
    except gittools.GitError as e:
        return f"git log failed: {e}"


def tool_gitcommit(params: dict, config: dict) -> str:
    from drydock import gittools
    try:
        return gittools.commit(
            _git_cwd(config),
            _as_text(params.get("message")),
            add_all=params.get("add_all", True) is not False,
        )
    except gittools.GitError as e:
        return f"git commit failed: {e}"


def _rmf_graph(config):
    from drydock import rmf_graph
    cwd = config.get("cwd") or os.getcwd()
    path = rmf_graph.graph_path(cwd)
    return rmf_graph, rmf_graph.RmfGraph.load(path), path


def _gattrs(g, nid) -> dict:
    n = g.get(nid)
    return n["attrs"] if n else {}


def _stig_path(params, config):
    p = (params.get("path") or "").strip()
    return _resolve_path(p, config) if p else None


def tool_stigrules(params: dict, config: dict) -> str:
    """List the rules in a STIG checklist (.ckl/.cklb), optionally by status."""
    from drydock import stig
    path = _stig_path(params, config)
    if not path:
        return "Error: StigRules needs a `path` to a .ckl/.cklb checklist."
    try:
        cl = stig.load(path)
    except Exception as e:  # noqa: BLE001
        return f"Could not read checklist: {e}"
    status = params.get("status")
    sf = stig.canonical_status(status) if status else None
    rules = [r for r in cl.rules if sf is None or r.status == sf]
    head = (f"{path}: {len(cl.rules)} rules — " +
            ", ".join(f"{k}={v}" for k, v in cl.counts().items()))
    lines = [head] + [f"  {r.summary()}" for r in rules[:400]]
    if len(rules) > 400:
        lines.append(f"  … +{len(rules) - 400} more")
    return "\n".join(lines)


def tool_stigrule(params: dict, config: dict) -> str:
    """Full detail of ONE STIG rule (check content + fix text) for assessment."""
    from drydock import stig
    path = _stig_path(params, config)
    rid = (params.get("rule_id") or "").strip()
    if not path or not rid:
        return "Error: StigRule needs `path` and `rule_id`."
    try:
        cl = stig.load(path)
    except Exception as e:  # noqa: BLE001
        return f"Could not read checklist: {e}"
    r = cl.by_id(rid)
    if r is None:
        return f"No rule {rid} in {path}."
    return (f"{r.group_id} ({r.rule_id}) — {r.title}\nSeverity: {r.severity}\n"
            f"Status: {r.status}\nCheck Content:\n{r.check_content}\n\n"
            f"Fix Text:\n{r.fix_text}")


def tool_stigset(params: dict, config: dict) -> str:
    """Set a STIG rule's status + finding details/comments and save the file."""
    from drydock import stig
    path = _stig_path(params, config)
    rid = (params.get("rule_id") or "").strip()
    if not path or not rid:
        return "Error: StigSet needs `path` and `rule_id`."
    status = params.get("status")
    if status and stig.canonical_status(status) not in stig.STATUSES:
        return f"status must be one of {stig.STATUSES}."
    try:
        cl = stig.load(path)
        ok = cl.update(rid, status=status, finding_details=params.get("finding_details"),
                       comments=params.get("comments"))
        if not ok:
            return f"No rule {rid} in {path}."
        cl.save(path)
    except Exception as e:  # noqa: BLE001
        return f"Could not update checklist: {e}"
    return f"✓ {rid} set to {stig.canonical_status(status) if status else 'unchanged'} in {path}."


def tool_graphquery(params: dict, config: dict) -> str:
    """Traverse the RMF typed ontology graph (read-only)."""
    rmf_graph, g, _ = _rmf_graph(config)
    if not g.nodes:
        return ("The RMF graph is empty. Run /rmf bootstrap to build the control "
                "backbone, and use GraphAdd to record components/relationships.")
    op = (params.get("op") or "").strip().lower()
    ident = (params.get("id") or "").strip()
    if op == "control":
        node = rmf_graph.control_id(ident)
        n = g.get(node)
        if not n:
            return f"No control {ident} in the graph."
        objs = [_gattrs(g, o).get("prose", "") for o in g.neighbors(node, "ASSESSES", direction="in")]
        impl = [_gattrs(g, c).get("name", c) for c in g.neighbors(node, "IMPLEMENTS", direction="in")]
        out = [f"{n['attrs'].get('control_id', ident)} — {n['attrs'].get('title','')} "
               f"(Family: {n['attrs'].get('family','')})"]
        if objs:
            out.append("Assessment objectives:\n" + "\n".join(f"  - {o}" for o in objs))
        out.append("Implemented by: " + (", ".join(impl) if impl else "(no components recorded)"))
        rules = [_gattrs(g, rn).get("rule_id", rn) for rn in g.neighbors(node, "SATISFIED_BY", direction="out")]
        if rules:
            out.append("Satisfied by STIG rules: " + ", ".join(rules))
        return "\n".join(out)
    if op == "family":
        ctrls = [_gattrs(g, c).get("control_id", c)
                 for c in g.of_type("Control")
                 if _gattrs(g, c).get("family", "").lower().startswith(ident.lower())
                 or ident.lower() in _gattrs(g, c).get("family", "").lower()]
        return f"Controls in '{ident}': " + (", ".join(sorted(ctrls)) or "(none)")
    if op in ("component", "implementers", "inherited"):
        comp = rmf_graph.component_id(ident)
        if op == "implementers":
            node = rmf_graph.control_id(ident)
            comps = [_gattrs(g, c).get("name", c) for c in g.neighbors(node, "IMPLEMENTS", direction="in")]
            return f"Components implementing {ident}: " + (", ".join(comps) or "(none)")
        if op == "inherited":
            inh = g.inherited_controls(comp)
            names = [_gattrs(g, c).get("control_id", c) if g.get(c) else c for c in inh]
            return (f"{ident} inherits {len(names)} control(s) from its parent system(s): "
                    + (", ".join(names) or "(none — no RESIDES_ON recorded)"))
        n = g.get(comp)
        if not n:
            return f"No component '{ident}' in the graph (add it with GraphAdd)."
        impl = [_gattrs(g, c).get("control_id", c) for c in g.neighbors(comp, "IMPLEMENTS", direction="out")]
        res = [_gattrs(g, p).get("name", p) for p in g.neighbors(comp, "RESIDES_ON", direction="out")]
        vulns = [_gattrs(g, v).get("vuln_id", v) for v in g.neighbors(comp, "AFFECTS", direction="in")]
        return (f"Component {ident} ({n['attrs'].get('os','')}): implements "
                f"{', '.join(impl) or 'none'}; resides on {', '.join(res) or 'nothing'}; "
                f"flaws {', '.join(vulns) or 'none'}.")
    return "GraphQuery ops: control <id> | family <id> | component <name> | implementers <control> | inherited <component>"


def tool_graphadd(params: dict, config: dict) -> str:
    """Record a typed fact in the RMF ontology graph (write)."""
    rmf_graph, g, path = _rmf_graph(config)
    op = (params.get("op") or "").strip().lower()
    comp = (params.get("component") or "").strip()
    if op == "component" and comp:
        g.add_node(rmf_graph.component_id(comp), "Component", name=comp,
                   os=params.get("os"), ip=params.get("ip"), data_type=params.get("data_type"))
        g.save(path); return f"Recorded component {comp}."
    if op == "implements" and comp and params.get("control"):
        g.add_node(rmf_graph.component_id(comp), "Component", name=comp)
        g.add_edge(rmf_graph.component_id(comp), "IMPLEMENTS", rmf_graph.control_id(params["control"]))
        g.save(path); return f"Recorded: {comp} IMPLEMENTS {params['control'].upper()}."
    if op == "resides_on" and comp and params.get("parent"):
        g.add_node(rmf_graph.component_id(comp), "Component", name=comp)
        g.add_node(rmf_graph.component_id(params["parent"]), "Component", name=params["parent"])
        g.add_edge(rmf_graph.component_id(comp), "RESIDES_ON", rmf_graph.component_id(params["parent"]))
        g.save(path); return f"Recorded: {comp} RESIDES_ON {params['parent']}."
    if op == "vulnerability" and params.get("id"):
        vid = f"vuln:{params['id'].lower()}"
        g.add_node(vid, "Vulnerability", vuln_id=params["id"], severity=params.get("severity"))
        if comp:
            g.add_node(rmf_graph.component_id(comp), "Component", name=comp)
            g.add_edge(vid, "AFFECTS", rmf_graph.component_id(comp))
        g.save(path); return f"Recorded vulnerability {params['id']}" + (f" affecting {comp}." if comp else ".")
    if op == "satisfies" and params.get("control") and params.get("rule"):
        cn, rn = rmf_graph.control_id(params["control"]), rmf_graph.rule_node(params["rule"])
        if not g.get(cn):  # ref node if the catalog isn't bootstrapped
            g.add_node(cn, "Control", control_id=params["control"].upper())
        g.add_node(rn, "STIGRule", rule_id=params["rule"])
        g.add_edge(cn, "SATISFIED_BY", rn)
        g.save(path); return f"Recorded: {params['control'].upper()} SATISFIED_BY {params['rule']}."
    return ("GraphAdd ops: component (`component`) | implements (`component`,`control`) | "
            "resides_on (`component`,`parent`) | vulnerability (`id`, optional `component`) | "
            "satisfies (`control`,`rule` — a STIG rule satisfies a NIST control).")


def tool_websearch(params: dict, config: dict) -> str:
    """Search the internet (DuckDuckGo). Read-only; clean message when offline."""
    from drydock import web

    query = _as_str_arg(params.get("query")).strip()
    if not query:
        return "Error: `WebSearch` needs a `query`."
    try:
        k = int(params.get("k") or 5)
    except (TypeError, ValueError):
        k = 5
    try:
        results = web.search(query, k=max(1, min(k, 10)))
    except web.WebError as e:
        return f"Web search unavailable: {e}. You appear to be offline — answer from your own knowledge."
    return web.format_search(query, results)


def tool_webfetch(params: dict, config: dict) -> str:
    """Fetch a URL and return readable text. Read-only; clean message offline."""
    from drydock import web

    url = _as_str_arg(params.get("url")).strip()
    if not url:
        return "Error: `WebFetch` needs a `url`."
    try:
        mc = int(params.get("max_chars") or 6000)
    except (TypeError, ValueError):
        mc = 6000
    try:
        text = web.fetch(url, max_chars=max(500, min(mc, 30000)))
    except web.WebError as e:
        return f"Could not fetch the page: {e}."
    return text or f"(no readable text extracted from {url})"


def tool_knowledge(params: dict, config: dict) -> str:
    """Query the project's GraphRAG knowledge base (built with /graphrag build).
    Read-only; returns the most relevant passages plus related graph entities.
    If no index exists, says so cleanly rather than erroring."""
    from drydock import graphrag

    query = _as_str_arg(params.get("query")).strip()
    if not query:
        return "Error: `Knowledge` needs a `query` describing what to look up."
    cwd = config.get("cwd") or os.getcwd()
    store = config.get("graphrag_store") or graphrag.default_store_path(cwd)
    index = graphrag.load_index(store)
    if index is None:
        return (
            "No knowledge base has been built yet. The user can build one with "
            "'/graphrag build <path>' (a file or directory of docs/code). Until "
            "then, answer from your own knowledge."
        )
    try:
        k = int(params.get("k") or 5)
    except (TypeError, ValueError):
        k = 5
    result = graphrag.query_index(index, query, k=max(1, min(k, 15)))
    return graphrag.format_results(result, query)


def tool_build_knowledge(params: dict, config: dict) -> str:
    """Build/extend the GraphRAG knowledge base from a file or directory of docs —
    the model-callable equivalent of `/graphrag build|add`, so the agent can do it
    itself when asked instead of telling the user to run a slash command."""
    from drydock import graphrag

    path = _as_str_arg(params.get("path")).strip()
    if not path:
        return ("Error: `BuildKnowledge` needs a `path` — a file or directory of "
                "documents/code to index.")
    mode = (_as_str_arg(params.get("mode")) or "build").strip().lower()
    if mode not in ("build", "add"):
        mode = "build"
    cwd = config.get("cwd") or os.getcwd()
    store = config.get("graphrag_store") or graphrag.default_store_path(cwd)
    try:
        fn = graphrag.build_index if mode == "build" else graphrag.add_to_index
        stats = fn([path], store, cwd=cwd)
    except Exception as e:  # noqa: BLE001 — report, never crash the agent loop
        return f"Error building the knowledge base from {path}: {e}"
    if not stats.get("chunks"):
        return (f"No indexable text found under {path}. Nothing was added. (Supported: "
                "text/code files, .docx, .pdf, .md, etc. Check the path is correct.)")
    verb = "Built" if mode == "build" else "Updated"
    return (f"{verb} the knowledge base: {stats['files']} files, {stats['chunks']} "
            f"chunks, {stats['entities']} entities. Search it with the Knowledge tool.")


# ── Register all tools ────────────────────────────────────────────────────

_TOOLS = [
    ("Read", tool_read, True),
    ("Screenshot", tool_screenshot, True),
    ("Write", tool_write, False),
    ("Edit", tool_edit, False),
    ("Bash", tool_bash, False),
    ("Glob", tool_glob, True),
    ("Grep", tool_grep, True),
    ("todo", tool_todo, False),
    ("task", tool_task, True),
    ("Dispatch", tool_dispatch, True),
    ("Worker", tool_worker, False),
    ("Knowledge", tool_knowledge, True),
    ("BuildKnowledge", tool_build_knowledge, False),
    ("GraphQuery", tool_graphquery, True),
    ("GraphAdd", tool_graphadd, False),
    ("StigRules", tool_stigrules, True),
    ("StigRule", tool_stigrule, True),
    ("StigSet", tool_stigset, False),
    ("WebSearch", tool_websearch, True),
    ("WebFetch", tool_webfetch, True),
    ("GitStatus", tool_gitstatus, True),
    ("GitDiff", tool_gitdiff, True),
    ("GitLog", tool_gitlog, True),
    ("GitCommit", tool_gitcommit, False),
]

def register_all():
    for schema in SCHEMAS:
        name = schema["name"]
        func = {
            "Read": tool_read, "ViewImage": tool_viewimage,
            "Screenshot": tool_screenshot,
            "Write": tool_write, "Edit": tool_edit,
            "Bash": tool_bash, "Glob": tool_glob, "Grep": tool_grep,
            "todo": tool_todo, "task": tool_task, "Dispatch": tool_dispatch,
            "Worker": tool_worker,
            "Consult": tool_consult, "Knowledge": tool_knowledge,
            "BuildKnowledge": tool_build_knowledge,
            "GraphQuery": tool_graphquery, "GraphAdd": tool_graphadd,
            "StigRules": tool_stigrules, "StigRule": tool_stigrule, "StigSet": tool_stigset,
            "WebSearch": tool_websearch, "WebFetch": tool_webfetch,
            "GitStatus": tool_gitstatus, "GitDiff": tool_gitdiff,
            "GitLog": tool_gitlog, "GitCommit": tool_gitcommit,
        }[name]
        # Read-only w.r.t. the parent's files (GitStatus/Diff/Log inspect only;
        # GitCommit + GraphAdd write).
        read_only = name in (
            "Read", "ViewImage", "Screenshot", "Glob", "Grep", "task", "Dispatch", "Consult",
            "Knowledge", "GraphQuery", "StigRules", "StigRule",
            "WebSearch", "WebFetch", "GitStatus", "GitDiff", "GitLog",
        )
        register(ToolDef(name=name, schema=schema, func=func, read_only=read_only))

register_all()
