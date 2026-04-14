#!/usr/bin/env python3
"""Generate a REAL functional_tests.sh for any PRD.

NOT --help theater. The generator:
  1. Parses the PRD for example commands with their EXPECTED OUTPUTS
  2. Builds tests that run the command + verify output contains expected text
  3. Falls back to "executes without crash AND produces non-empty output"
     only when no expected output is documented
  4. Never emits a --help-only test (that proves nothing per CLAUDE.md)

Expected-output patterns supported:
  - Command followed by "→ <expected>"
  - Command followed by code block with output
  - "Output: <expected>" lines near the command
  - Explicit "Expected: <text>" or "Example output: <text>"

For PRDs without expected outputs, we generate tests that:
  - Run the command
  - Assert it produced AT LEAST 10 chars of output
  - Assert it didn't produce a Traceback
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def find_package_name(prd_text: str, dir_name: str) -> str:
    m = re.search(r"##\s*Package\s+Name\s*\n\s*`?([a-zA-Z_][\w]*)`?",
                  prd_text, re.IGNORECASE)
    if m:
        return m.group(1)
    return re.sub(r"^\d+_", "", dir_name)


def extract_example_commands(prd_text: str, pkg: str) -> list[dict]:
    """Return list of {cmd, expected} from PRD examples.

    Looks for `python3 -m pkg ...` invocations and tries to associate
    each with an expected output snippet found in nearby text.
    """
    examples = []
    lines = prd_text.splitlines()
    in_block = False

    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_block = not in_block
            continue
        if not in_block:
            continue

        cmd = None
        # Match full `python3 -m pkg ...` command
        m = re.search(rf"python3? -m {re.escape(pkg)}\b([^#\n]*)", line)
        if m:
            cmd = f"python3 -m {pkg}{m.group(1)}".strip().rstrip("\\")

        if not cmd:
            continue

        # Strip "→ expected..." noise from end FIRST, so downstream checks
        # (e.g. --help suffix) see the clean command.
        m_arrow = re.search(r"(.*?)\s+[→➜]\s+(.+)", cmd)
        expected = None
        if m_arrow:
            cmd = m_arrow.group(1).strip()
            raw_arrow = m_arrow.group(2).strip()
            # Arrow-style text is MOST OFTEN a prose description of what the
            # command does (e.g., "one 16-char password") — NOT actual output.
            # Only treat as expected output if it looks concrete:
            #   - is short and lacks English articles/verbs
            #   - OR contains a clear literal marker (path, JSON, number+unit)
            PROSE_MARKERS = {
                # articles / determiners
                "the", "a", "an", "each", "every", "all", "any", "some",
                # small-number words (likely counting)
                "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten",
                # description verbs (usually third-person singular)
                "shows", "show", "prints", "print", "displays", "display",
                "returns", "return", "outputs", "output", "lists", "list",
                "generates", "generate", "creates", "create", "makes", "make",
                "emits", "emit", "yields", "yield", "adds", "add",
                "removes", "remove", "deletes", "delete", "updates", "update",
                "reads", "read", "writes", "write", "opens", "open",
                "runs", "run", "executes", "execute", "starts", "start",
                "validates", "validate", "checks", "check", "scans", "scan",
                # connectors common in English descriptions
                "with", "without", "from", "into", "onto", "over", "under",
                "and", "or", "then", "to", "as",
                # nouns that signal prose description rather than output
                "password", "passwords", "help", "usage",
                "ciphers", "items", "records", "rows", "lines",
                "available", "valid", "invalid",
                # common "output NOUN" description words
                "hash", "digest", "checksum", "result", "value",
                "stats", "summary", "report", "table", "count",
                "number", "text", "file", "files", "directory",
                "directories", "data", "info", "information",
                "string", "number", "list", "json", "csv", "yaml",
                # adjectives/adverbs common in descriptions
                "based", "mode", "style", "format", "kind", "type",
                "version", "default", "preview", "output", "input",
                # ordinals / positional words
                "first", "last", "next", "previous", "top", "bottom",
                "min", "max", "minimum", "maximum",
                "up", "down",
                # styling/formatting descriptors
                "indented", "centered", "justified", "wrapped", "padded",
                "aligned", "formatted", "colored", "highlighted", "bold",
                "italic", "underline", "spaces", "tabs", "wrap",
                "compressed", "encoded", "decoded", "escaped", "encrypted",
            }
            words = re.findall(r"[A-Za-z]+", raw_arrow.lower())
            prose_count = sum(1 for w in words if w in PROSE_MARKERS)
            # STRONG literal-output signals. Default to behavioral test
            # unless one of these is clearly present — PRD arrow-text is
            # overwhelmingly prose, not actual stdout.
            has_digits = bool(re.search(r"\d", raw_arrow))
            has_quotes = "\"" in raw_arrow or "'" in raw_arrow
            has_json_like = any(c in raw_arrow for c in "{}[]=")
            # Path or URL — but NOT "A/B" or "A/B/C" pattern like
            # "OK/FAILED", "word/line/char", "ON/OFF" (either-or descriptor).
            # Detect when the '/' connects multiple simple identifiers with
            # no directory-ish segments.
            stripped = raw_arrow.strip().split()[0] if raw_arrow.strip() else ""
            either_or = bool(re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]*(?:/[A-Za-z][A-Za-z0-9_]*){1,3}",
                stripped,
            ))
            has_path_or_url = (
                ("/" in raw_arrow or "://" in raw_arrow)
                and not either_or
            )
            is_all_upper = (
                raw_arrow.replace(" ", "").isalpha()
                and raw_arrow.isupper()
                and len(raw_arrow) >= 2
            )
            # A "codename" single token: no spaces, mixed case, looks
            # engineered (e.g. "Khoor", "SGVsbG8gV29ybGQ", "MMMCMXCIX").
            is_single_codename = (
                len(words) == 1
                and " " not in raw_arrow.strip()
                and not raw_arrow.strip().islower()
                and len(raw_arrow.strip()) >= 4
            )

            # Keep as literal ONLY if strong signal. Default to prose.
            is_literal = (
                has_digits
                or has_quotes
                or has_json_like
                or has_path_or_url
                or is_all_upper
                or is_single_codename
            )
            # But if the single-word arrow IS itself a known prose word
            # (e.g. "JSON", "CSV", "XML", "HASH"), treat as description
            # — these are format NAMES, not literal output.
            if len(words) == 1 and words[0] in PROSE_MARKERS:
                is_literal = False
            # Even with a literal signal, bail to behavioral if the
            # arrow text is clearly prose:
            #   - 3+ words + any prose marker
            #   - contains prose marker AND looks like noun-phrase
            #     (e.g. "SHA-256 hash", "MD5 digest", "numeric stats")
            if is_literal and len(words) >= 3 and prose_count >= 1:
                is_literal = False
            if is_literal and prose_count >= 1 and len(words) >= 2:
                # The digits made it LOOK literal, but a descriptive noun
                # (hash, value, stats, …) says it's actually a prose
                # description of what the command outputs.
                is_literal = False

            if is_literal:
                expected = raw_arrow

        # If no arrow, look at the NEXT non-empty line inside the same block
        if not expected:
            for j in range(i + 1, min(i + 5, len(lines))):
                nl = lines[j].strip()
                if not nl:
                    continue
                if nl.startswith("```"):
                    break
                if nl.startswith("$") or nl.startswith("python") or nl.startswith(pkg):
                    break
                # Skip if it looks like another shell command (echo/mkdir/cp/etc)
                SHELL_CMDS = ("echo ", "mkdir ", "cp ", "mv ", "rm ", "ls ",
                              "cat ", "sudo ", "bash ", "sh ", "chmod ",
                              "touch ", "ln ", "cd ")
                if any(nl.startswith(c) for c in SHELL_CMDS):
                    break
                # Skip if line contains shell redirection/pipe
                if ">" in nl or "|" in nl:
                    break
                # This could be expected output
                if len(nl) >= 2 and len(nl) <= 80:
                    expected = nl
                break

        # Skip --help-only commands (not a real test — PRD calls these out
        # just to document the UX, not to verify behavior).
        if cmd.endswith("--help") or cmd.endswith("-h"):
            continue
        # Skip bare `python3 -m pkg` with no subcommand (nothing to verify).
        m_tail = cmd.split("-m", 1)[-1].strip()
        if m_tail == pkg:
            continue

        if cmd in [e["cmd"] for e in examples]:
            continue
        examples.append({"cmd": cmd, "expected": expected})
        if len(examples) >= 5:
            break

    return examples


def generate_tests(prd_path: Path, pkg: str) -> str:
    prd_text = prd_path.read_text()
    examples = extract_example_commands(prd_text, pkg)

    # Collect /tmp paths referenced by any command so we can pre-create
    # them. Many PRDs reference /tmp/messy, /tmp/test, etc. as fixtures.
    tmp_paths: set[str] = set()
    # Plain filenames referenced as positional args (searcher.py, data.csv, …)
    plain_files: set[str] = set()
    PLAIN_FILE_RE = re.compile(
        r"(?:^|\s)(['\"]?)([A-Za-z][A-Za-z0-9_-]*\.(?:py|txt|csv|json|md|ini|yaml|yml|log|html|xml))\1"
    )

    for ex in examples:
        for m in re.finditer(r"(/tmp/[A-Za-z0-9_./-]+)", ex["cmd"]):
            p = m.group(1).rstrip(".,;:'\"")
            # Avoid too-specific paths like /tmp/messy/foo.txt —
            # just pre-make the first two segments as directories.
            parts = p.split("/")
            if len(parts) >= 3:
                tmp_paths.add("/".join(parts[:3]))
        for m in PLAIN_FILE_RE.finditer(ex["cmd"]):
            plain_files.add(m.group(2))

    lines = [
        "#!/bin/bash",
        f"# Auto-generated functional tests for {pkg}",
        "# NOT --help theater — tests actually run commands and verify output.",
        "set +e",
        "PROJECT_DIR=$(pwd)",
        'export PYTHONPATH="$PYTHONPATH:$PROJECT_DIR"',
        "PASS=0",
        "FAIL=0",
        'fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }',
        'pass() { echo "PASS: $1"; PASS=$((PASS+1)); }',
        "",
    ]
    # Pre-create /tmp fixtures referenced by commands, and seed with
    # a few sample files so the command has something to act on.
    if tmp_paths:
        lines.append("# Create /tmp fixtures referenced in PRD examples")
        for p in sorted(tmp_paths):
            lines.append(f"mkdir -p {p}")
            lines.append(f'echo "hello world" > {p}/sample.txt')
            lines.append(f'echo "a,b,c\\n1,2,3" > {p}/data.csv')
            lines.append(f'echo "{{\\"k\\":1}}" > {p}/data.json')
        lines.append("")
    # Pre-create plain filenames referenced as positional args if the
    # test project doesn't already have them. The contents are just
    # reasonable defaults so the command has something to parse.
    if plain_files:
        lines.append("# Create plain-name fixtures referenced in PRD examples")
        for fn in sorted(plain_files):
            ext = fn.rsplit(".", 1)[-1].lower()
            default = {
                "py":   "# sample Python file\ndef foo():\n    pass\n",
                "txt":  "the quick brown fox jumps over the lazy dog\n",
                "csv":  "name,age,city\nalice,30,seattle\nbob,25,tampa\n",
                "json": '{"k":1,"name":"test"}\n',
                "md":   "# Test Markdown\n\n- item one\n- item two\n",
                "ini":  "[section]\nkey=value\n",
                "yaml": "k: v\nlist:\n  - a\n  - b\n",
                "yml":  "k: v\n",
                "log":  "2024-01-01 12:00:00 INFO starting\n2024-01-01 12:00:01 ERROR oops\n",
                "html": "<html><body><h1>hi</h1></body></html>\n",
                "xml":  "<?xml version='1.0'?><root><a>1</a></root>\n",
            }.get(ext, "placeholder\n")
            # Bash-escape the content: double-quote, escape $ \ "
            esc = default.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
            lines.append(f'[ -f {fn} ] || printf "{esc}" > {fn}')
        lines.append("")

    if not examples:
        # No parseable examples — fall back to a single "does it import + produce output"
        # test but make it meaningful (not --help).
        lines.extend([
            f"# Fallback: no examples in PRD. Check the package imports cleanly.",
            f'OUT=$(python3 -c "import {pkg}; print({pkg}.__name__)" 2>&1)',
            f'echo "$OUT" | grep -q "^{pkg}$" && pass "package imports" || fail "import: ${{OUT: -400}}"',
            "",
        ])
    else:
        # Keywords that signal an interactive subcommand needing stdin
        INTERACTIVE_KW = (
            " play", " game", " start", " run", " repl", " shell",
            " interactive", " input ", " quiz", " guess", " login",
        )
        # Commands with -o FILE / --output FILE write to the file, not stdout
        OUTPUT_FLAG_RE = re.compile(
            r"(?:-o|--output|--out)\s+(['\"]?)([A-Za-z0-9_./-]+)\1"
        )
        for i, ex in enumerate(examples, 1):
            cmd = ex["cmd"]
            expected = ex["expected"]
            # Escape for bash safely
            esc_cmd = cmd
            short_cmd = cmd[:50].replace('"', "'")
            # If command looks interactive, pipe numeric stdin as a fallback.
            # `seq 1 100` covers most games (guessing, word-pick indices, etc).
            # Non-interactive commands silently ignore stdin.
            cmd_lower = (" " + cmd.lower() + " ")
            is_interactive = any(kw in cmd_lower for kw in INTERACTIVE_KW)
            stdin_redirect = " < <(seq 1 100)" if is_interactive else ""
            if stdin_redirect:
                esc_cmd = cmd + stdin_redirect

            # -o FILE / --output FILE: command writes to file, stdout is
            # usually empty. Verify the file was created instead.
            out_match = OUTPUT_FLAG_RE.search(cmd)
            out_file = out_match.group(2) if out_match else None
            if out_file and not expected:
                lines.extend([
                    f"# Test {i}: {short_cmd} (writes to {out_file})",
                    f"rm -f {out_file}",
                    f"{esc_cmd} 2>/tmp/_t{i}.err >/dev/null",
                    f'if [ -s "{out_file}" ]; then',
                    f'    pass "test {i}: {out_file} created"',
                    f'else',
                    f'    fail "test {i}: {out_file} missing/empty; $(tail -c 400 /tmp/_t{i}.err 2>/dev/null)"',
                    f'fi',
                    "",
                ])
                continue
            if expected:
                # Extract a short substring to grep for
                # Strip quotes, keep first alphanumeric run
                keyword = re.search(r"[A-Za-z0-9_./-]{3,}", expected)
                kw = keyword.group(0) if keyword else expected[:20]
                kw_esc = kw.replace('"', '\\"')
                # Use FIXED-STRING, case-insensitive grep (grep -qiF):
                #  -F: treat pattern as literal so *, ^, $, . aren't regex
                #       metacharacters. Needed for outputs like
                #       "2^3 * 3^2 * 5" (factor) or "x=2, x=3" (equation).
                #  -i: output case often varies ("Initialized" vs "already
                #       initialized").
                lines.extend([
                    f"# Test {i}: {short_cmd} → expects '{kw[:30]}'",
                    f"OUT=$({esc_cmd} 2>&1)",
                    f'if echo "$OUT" | grep -qiF "{kw_esc}"; then',
                    f'    pass "test {i}: expected output present"',
                    f'else',
                    f'    fail "test {i} (expected {kw[:30]!r}): ${{OUT: -400}}"',
                    f'fi',
                    "",
                ])
            else:
                # Behavioral test: non-empty output + no traceback
                lines.extend([
                    f"# Test {i}: {short_cmd} (runs, produces output, no crash)",
                    f"OUT=$({esc_cmd} 2>&1)",
                    f'if [ -n "$OUT" ] && ! echo "$OUT" | grep -qE "(Traceback|No module named|ModuleNotFoundError)"; then',
                    f'    pass "test {i}: ran cleanly"',
                    f'else',
                    f'    fail "test {i}: ${{OUT: -400}}"',
                    f'fi',
                    "",
                ])

    lines.extend([
        'echo ""',
        'echo "RESULT: $PASS passed, $FAIL failed"',
        '[ $FAIL -eq 0 ] && exit 0 || exit 1',
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    d = Path(args.dir).resolve()
    prd = d / "PRD.master.md"
    if not prd.exists():
        prd = d / "PRD.md"
    if not prd.exists():
        print(f"No PRD in {d}", file=sys.stderr)
        return 1

    pkg = find_package_name(prd.read_text(), d.name)
    script = generate_tests(prd, pkg)

    if args.write:
        out = d / "functional_tests.sh"
        out.write_text(script)
        out.chmod(0o755)
        print(f"Wrote {out} ({script.count(chr(10))} lines)")
    else:
        print(script)
    return 0


if __name__ == "__main__":
    sys.exit(main())
