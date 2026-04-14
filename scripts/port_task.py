#!/usr/bin/env python3
"""Port-task harness — have drydock translate code between languages.

The core idea: pick a working codebase in language A, generate a differential
test suite (actual I/O pairs from running language-A code), then ask drydock
to port to language B. Validate by running language-B build against the
same input args and comparing byte-for-byte.

This dodges the weak-test problem we have with auto-generated functional
tests: the oracle is the ORIGINAL program's output, not a keyword grep.

Pipeline:
  1. Collect inputs (args the PRD documents, or sampled values for numeric
     tasks like roman numeral conversion).
  2. Run language-A binary for each input → capture (stdout, exit_code).
  3. Write diff_tests.sh that runs language-B binary for each input and
     diffs against the captured output.
  4. Drive drydock (via meta_ralph-style loop) to port the source until
     diff_tests.sh fully passes.

Usage:
    python3 scripts/port_task.py \\
        --source /data3/drydock_test_projects/01_roman_converter \\
        --source-pkg roman_converter \\
        --target /data3/drydock_test_projects/port/roman_converter_cpp \\
        --target-lang cpp \\
        --budget-minutes 60
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


LANG_CONFIG = {
    "cpp": {
        "build":   "make",
        "binary":  "./main",
        "hint":    "Use g++17. Single Makefile with 'make' target that builds ./main. Use std:: containers; read argv; write to stdout.",
        "files":   "src/*.cpp, include/*.h, Makefile",
    },
    "node": {
        "build":   "",  # no build needed
        "binary":  "node src/main.js",
        "hint":    "Use Node.js (no external deps). Entry point src/main.js reads process.argv and writes to stdout.",
        "files":   "src/*.js, package.json (no deps)",
    },
    "rust": {
        "build":   "cargo build --release 2>/dev/null",
        "binary":  "./target/release/main",
        "hint":    "Use Rust stable, std-only. Cargo.toml with name=main. Single src/main.rs.",
        "files":   "src/main.rs, Cargo.toml",
    },
}


def collect_sample_inputs(source_dir: Path, pkg: str) -> list[list[str]]:
    """Extract sample CLI invocations from the PRD."""
    inputs: list[list[str]] = []
    prd = source_dir / "PRD.master.md"
    if not prd.exists():
        prd = source_dir / "PRD.md"
    if not prd.exists():
        return inputs
    import re
    text = prd.read_text()
    # Find python3 -m pkg lines inside code blocks
    in_block = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_block = not in_block
            continue
        if not in_block:
            continue
        m = re.search(rf"python3? -m {re.escape(pkg)}\b([^#\n→]*)", line)
        if m:
            tail = m.group(1).strip().rstrip("\\")
            if not tail:
                continue
            if "--help" in tail:
                continue
            try:
                args = shlex.split(tail)
            except ValueError:
                continue
            if args:
                inputs.append(args)
    # Dedupe
    seen, out = set(), []
    for a in inputs:
        key = tuple(a)
        if key not in seen:
            seen.add(key); out.append(a)
    return out[:10]


def run_source_binary(source_dir: Path, pkg: str, args: list[str]) -> tuple[str, int]:
    """Run `python3 -m pkg <args>` against the source. Returns (stdout, rc)."""
    cmd = ["python3", "-m", pkg] + args
    try:
        r = subprocess.run(cmd, cwd=source_dir, capture_output=True, text=True,
                           timeout=30, env={"PYTHONPATH": str(source_dir)})
        return r.stdout, r.returncode
    except subprocess.TimeoutExpired:
        return "", 124


def write_diff_tests(target_dir: Path, target_lang: str,
                     samples: list[tuple[list[str], str, int]]) -> None:
    """Write diff_tests.sh into target_dir.

    Each sample is (args, expected_stdout, expected_rc). The test script
    runs the target binary with those args and diffs byte-for-byte.
    """
    cfg = LANG_CONFIG[target_lang]
    lines = [
        "#!/bin/bash",
        f"# Differential tests against reference Python implementation.",
        "set +e",
        "PASS=0",
        "FAIL=0",
        'pass() { echo "PASS: $1"; PASS=$((PASS+1)); }',
        'fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }',
        "",
        "# Build the target",
    ]
    if cfg["build"]:
        lines.extend([
            f'{cfg["build"]} 2>/tmp/build.log',
            f'if [ $? -ne 0 ]; then fail "build failed"; echo; echo "RESULT: 0 passed, 1 failed"; exit 1; fi',
            "",
        ])
    # Detect the actual binary — drydock may choose a different name than
    # the default `./main`. Look for ./main first, then any executable file.
    if cfg["binary"].startswith("./"):
        lines.extend([
            "# Detect the binary (drydock may use any name)",
            'BIN=""',
            'for candidate in ./main ' + ' '.join(f'./{w}' for w in ['a.out', 'app', 'run']) + '; do',
            '    if [ -x "$candidate" ]; then BIN="$candidate"; break; fi',
            'done',
            'if [ -z "$BIN" ]; then',
            '    # Fall back: first executable non-shell non-text file in cwd',
            '    for f in *; do',
            '        if [ -f "$f" ] && [ -x "$f" ] && ! head -c4 "$f" | grep -q "^#!"; then',
            '            BIN="./$f"; break',
            '        fi',
            '    done',
            'fi',
            'if [ -z "$BIN" ]; then fail "no binary found"; echo; echo "RESULT: 0 passed, 1 failed"; exit 1; fi',
            'echo "using binary: $BIN"',
            "",
        ])
    # Write each sample as a test
    for i, (args, expected, rc) in enumerate(samples, 1):
        argstr = " ".join(shlex.quote(a) for a in args)
        # Write expected to a tmp file (byte-for-byte comparison).
        expfile = f"/tmp/port_{i}.expected"
        # Encode expected as base64 inline to survive bash quoting
        import base64
        b64 = base64.b64encode(expected.encode()).decode()
        bin_expr = "$BIN" if cfg["binary"].startswith("./") else cfg["binary"]
        lines.extend([
            f"# Test {i}: {bin_expr} {argstr[:60]}",
            f'echo "{b64}" | base64 -d > {expfile}',
            f'ACTUAL=$({bin_expr} {argstr} 2>&1); ACTUAL_RC=$?',
            f'EXPECTED=$(cat {expfile})',
            f'if [ "$ACTUAL" = "$EXPECTED" ]; then',
            f'    pass "test {i}: matches reference"',
            f'else',
            f'    # Show a short diff hint for debugging',
            f'    fail "test {i}: expected {len(expected)} bytes, got ${{#ACTUAL}} bytes; first diff: $(diff <(echo -n \"$EXPECTED\") <(echo -n \"$ACTUAL\") | head -4)"',
            f'fi',
            "",
        ])
    lines.extend([
        'echo ""',
        'echo "RESULT: $PASS passed, $FAIL failed"',
        '[ $FAIL -eq 0 ] && exit 0 || exit 1',
    ])
    (target_dir / "diff_tests.sh").write_text("\n".join(lines) + "\n")
    (target_dir / "diff_tests.sh").chmod(0o755)


def write_task_prd(target_dir: Path, source_dir: Path, source_pkg: str,
                   target_lang: str) -> None:
    """Write a PRD.md into target_dir describing what drydock must do."""
    cfg = LANG_CONFIG[target_lang]
    prd = f"""# Port Task — {source_pkg} → {target_lang}

## Source
Python package: `{source_dir}/{source_pkg}/`
Run reference: `cd {source_dir} && python3 -m {source_pkg} <args>`

## Target
Language: {target_lang}
Expected layout: {cfg["files"]}
Build: `{cfg["build"] or "(no build step)"}`
Run: `{cfg["binary"]} <args>`

## Rule
The target binary must produce BYTE-IDENTICAL stdout to the Python reference
for every input in diff_tests.sh. No keyword matching — full string equality.

## Validation
`bash diff_tests.sh` must pass. Each test compares full stdout bytes.

## Constraints
- {cfg["hint"]}
- No external dependencies beyond stdlib.
- Match argv parsing exactly: same flags, same positional args, same error
  messages (where tested).
- Preserve newline behavior: if Python ends with \\n, so must yours.

## Workflow
1. Read the source: `{source_dir}/{source_pkg}/*.py`
2. Understand the CLI contract.
3. Translate module-by-module to {target_lang}.
4. Run `bash diff_tests.sh` to see what fails.
5. Iterate until 100% pass.
"""
    (target_dir / "PRD.md").write_text(prd)
    # Also save as master for restoration between runs.
    (target_dir / "PRD.master.md").write_text(prd)


def setup_port_task(source_dir: Path, source_pkg: str,
                    target_dir: Path, target_lang: str) -> int:
    """Prepare the target directory: collect samples, write tests + PRD."""
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"[setup] collecting sample inputs from {source_dir.name}/PRD")
    sample_args = collect_sample_inputs(source_dir, source_pkg)
    if not sample_args:
        print("[setup] WARNING: no sample inputs found in PRD")

    samples: list[tuple[list[str], str, int]] = []
    for args in sample_args:
        stdout, rc = run_source_binary(source_dir, source_pkg, args)
        samples.append((args, stdout, rc))
        print(f"  captured: {args} → {len(stdout)} bytes, rc={rc}")

    if not samples:
        print("[setup] no samples captured — aborting")
        return 1

    write_diff_tests(target_dir, target_lang, samples)
    write_task_prd(target_dir, source_dir, source_pkg, target_lang)
    link = target_dir / "source_link"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(source_dir / source_pkg, target_is_directory=True)
    # Save the raw samples for debugging
    raw = [{"args": a, "stdout": s, "rc": r} for a, s, r in samples]
    (target_dir / "_samples.json").write_text(json.dumps(raw, indent=2))
    print(f"[setup] target at {target_dir}")
    print(f"[setup] {len(samples)} test samples written")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="source PRD directory")
    ap.add_argument("--source-pkg", required=True, help="python package name")
    ap.add_argument("--target", required=True, help="target directory")
    ap.add_argument("--target-lang", choices=list(LANG_CONFIG), default="cpp")
    ap.add_argument("--setup-only", action="store_true",
                    help="only write PRD+tests, don't launch drydock")
    ap.add_argument("--budget-minutes", type=int, default=60)
    args = ap.parse_args()

    source = Path(args.source).resolve()
    target = Path(args.target).resolve()
    rc = setup_port_task(source, args.source_pkg, target, args.target_lang)
    if rc != 0 or args.setup_only:
        return rc

    # Launch mega_loop on the target dir — reuses the iteration machinery.
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "mega_loop.py"),
        "--cwd", str(target),
        "--pkg", args.source_pkg,  # package name inside target dir
        "--budget-minutes", str(args.budget_minutes),
        "--no-progress-minutes", "15",
    ]
    # We cheat: mega_loop expects `functional_tests.sh`; rename.
    (target / "functional_tests.sh").write_text(
        (target / "diff_tests.sh").read_text()
    )
    (target / "functional_tests.sh").chmod(0o755)
    r = subprocess.run(cmd)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
