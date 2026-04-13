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
        # Skip --help-only commands (not a real test)
        if cmd.endswith("--help") or cmd.endswith("-h"):
            continue
        if " " not in cmd.split("-m")[1].strip():
            # No subcommand/args (bare `python3 -m pkg`) — skip
            if cmd.endswith(f"-m {pkg}"):
                continue

        # Strip "→ expected..." noise from end
        m_arrow = re.search(r"(.*?)\s+[→➜]\s+(.+)", cmd)
        expected = None
        if m_arrow:
            cmd = m_arrow.group(1).strip()
            expected = m_arrow.group(2).strip()

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
                # This could be expected output
                # Take just the first line's substring
                if len(nl) >= 2 and len(nl) <= 80:
                    expected = nl
                break

        if cmd in [e["cmd"] for e in examples]:
            continue
        examples.append({"cmd": cmd, "expected": expected})
        if len(examples) >= 5:
            break

    return examples


def generate_tests(prd_path: Path, pkg: str) -> str:
    prd_text = prd_path.read_text()
    examples = extract_example_commands(prd_text, pkg)

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
        for i, ex in enumerate(examples, 1):
            cmd = ex["cmd"]
            expected = ex["expected"]
            # Escape for bash safely
            esc_cmd = cmd
            short_cmd = cmd[:50].replace('"', "'")
            if expected:
                # Extract a short substring to grep for
                # Strip quotes, keep first alphanumeric run
                keyword = re.search(r"[A-Za-z0-9_./-]{3,}", expected)
                kw = keyword.group(0) if keyword else expected[:20]
                kw_esc = kw.replace('"', '\\"')
                lines.extend([
                    f"# Test {i}: {short_cmd} → expects '{kw[:30]}'",
                    f"OUT=$({esc_cmd} 2>&1)",
                    f'if echo "$OUT" | grep -q "{kw_esc}"; then',
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
                    f'if [ "${{#OUT}}" -ge 10 ] && ! echo "$OUT" | grep -q "Traceback"; then',
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
