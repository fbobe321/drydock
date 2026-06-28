"""Built-in tools for DryDock v3.

Tools: Read, Write, Edit, Bash, Glob, Grep
Each tool is a function (params, config) -> str.
"""
from __future__ import annotations

import os
import re
import difflib
import glob as _glob
import signal
import subprocess
import time
from pathlib import Path

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
        "description": "Replace exact text in a file. old_string must match exactly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string", "description": "Exact text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
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


def tool_read(params: dict, config: dict) -> str:
    fp = _resolve_path(params["file_path"], config)
    limit = params.get("limit")  # None = caller didn't specify a window
    offset = params.get("offset", 0)
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Huge file, no explicit window → index it instead of dumping it.
        if limit is None and offset == 0 and len(lines) > _BIG_FILE_LINES:
            return _file_index(lines, fp)
        eff_limit = 2000 if limit is None else limit
        selected = lines[offset:offset + eff_limit]
        numbered = [f"{i + offset + 1}\t{line.rstrip()}" for i, line in enumerate(selected)]
        result = "\n".join(numbered)
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
    content = params.get("content", "")
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
    old = params.get("old_string", "")
    new = params.get("new_string", "")
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
        if count > 1:
            return f"Error: old_string found {count} times in {fp}. Add more context to make it unique."
        updated = content.replace(old, new, 1)
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
    timeout = params.get("timeout", 120)
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
    try:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=config.get("cwd"), start_new_session=True,
        )
        config.setdefault("_abort", {})["proc"] = proc
        start = time.monotonic()
        while True:
            try:
                out, _ = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    kill_process_group(proc)
                    proc.communicate()
                    return "[stopped by user]"
                if time.monotonic() - start > timeout:
                    kill_process_group(proc)
                    proc.communicate()
                    bigger = min(timeout * 4, 1800)
                    msg = (
                        f"Error: command timed out after {timeout}s. If it is "
                        f"legitimately slow (a big query, build, download, or "
                        f"test run), retry with a larger timeout — pass "
                        f"timeout: {bigger}. Otherwise it may be hung."
                    )
                    if _is_network_command(cmd):
                        msg += _OFFLINE_HINT
                    return msg
        output = out or ""
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
    pattern = params["pattern"]
    base = params.get("path") or config.get("cwd") or "."
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
    pattern = params["pattern"]
    path = params.get("path") or config.get("cwd") or "."
    include = params.get("include", "")
    try:
        cmd = ["grep", "-rn", "--color=never"]
        if include:
            cmd.extend(["--include", include])
        cmd.extend([pattern, path])
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
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
SUBAGENT_TOOLS = ("Read", "Glob", "Grep", "Bash")

_SUBAGENT_SYSTEM = (
    "You are a focused exploration sub-agent inside Drydock. You have read-only "
    "tools: Read, Glob, Grep, and Bash (use Bash only to INSPECT — ls, cat, "
    "grep, find, git log — never to modify files). Investigate the task you are "
    "given, then STOP and reply with a concise, factual summary of what you "
    "found: concrete file:line references and the key code, not narration. Do "
    "NOT try to edit or create files — the main agent acts on your findings."
)


def _run_subagent(prompt: str, config: dict) -> str:
    """Run one read-only sub-agent to completion and return its final summary.
    Shared by `task` (one) and `Dispatch` (many in parallel). Hard-capped; never
    raises (a sub-agent must not crash the parent turn)."""
    from drydock.agent import run as agent_run, AgentState, TurnDone

    sub_state = AgentState()
    sub_config = dict(config)
    sub_config["tool_allowlist"] = list(SUBAGENT_TOOLS)
    sub_config["force_first_tool"] = False
    sub_config["max_turns"] = 24       # bound the helper hard
    sub_config["max_tool_calls"] = 20
    sub_config.pop("_todo", None)      # the sub-agent keeps no checklist of its own
    sub_config.pop("_plan_autocontinue", None)
    # Own abort holder so parallel sub-agents don't clobber each other's (or the
    # parent's) in-flight client/proc handles in the shared dict.
    sub_config["_abort"] = {}
    steps = 0
    try:
        for ev in agent_run(prompt, sub_state, sub_config, _SUBAGENT_SYSTEM):
            if isinstance(ev, TurnDone):
                steps += 1
    except Exception as e:  # a sub-agent must never crash the parent turn
        return f"[sub-agent error: {e}]"
    for msg in reversed(sub_state.messages):
        if msg.get("role") == "assistant" and (msg.get("content") or "").strip():
            return msg["content"].strip()
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
            params.get("message") or "",
            add_all=params.get("add_all", True) is not False,
        )
    except gittools.GitError as e:
        return f"git commit failed: {e}"


def tool_websearch(params: dict, config: dict) -> str:
    """Search the internet (DuckDuckGo). Read-only; clean message when offline."""
    from drydock import web

    query = (params.get("query") or "").strip()
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

    url = (params.get("url") or "").strip()
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

    query = (params.get("query") or "").strip()
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


# ── Register all tools ────────────────────────────────────────────────────

_TOOLS = [
    ("Read", tool_read, True),
    ("Write", tool_write, False),
    ("Edit", tool_edit, False),
    ("Bash", tool_bash, False),
    ("Glob", tool_glob, True),
    ("Grep", tool_grep, True),
    ("todo", tool_todo, False),
    ("task", tool_task, True),
    ("Dispatch", tool_dispatch, True),
    ("Knowledge", tool_knowledge, True),
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
            "Read": tool_read, "Write": tool_write, "Edit": tool_edit,
            "Bash": tool_bash, "Glob": tool_glob, "Grep": tool_grep,
            "todo": tool_todo, "task": tool_task, "Dispatch": tool_dispatch,
            "Knowledge": tool_knowledge,
            "WebSearch": tool_websearch, "WebFetch": tool_webfetch,
            "GitStatus": tool_gitstatus, "GitDiff": tool_gitdiff,
            "GitLog": tool_gitlog, "GitCommit": tool_gitcommit,
        }[name]
        # Read-only w.r.t. the parent's files (GitStatus/Diff/Log inspect only;
        # GitCommit writes a local, reversible commit).
        read_only = name in (
            "Read", "Glob", "Grep", "task", "Dispatch", "Knowledge",
            "WebSearch", "WebFetch", "GitStatus", "GitDiff", "GitLog",
        )
        register(ToolDef(name=name, schema=schema, func=func, read_only=read_only))

register_all()
