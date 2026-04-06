#!/usr/bin/env python3
"""Drive the REAL DryDock TUI with pexpect and capture everything.

This runs the actual TUI binary, types into it, watches for output,
and detects loops, errors, and completion.

Usage:
    python3 scripts/tui_test.py "Read PRD.md and build log_analyzer" --cwd /data3/test_drydock --timeout 300
"""
import pexpect
import sys
import time
import re
import os
import argparse

DRYDOCK_BIN = "/home/bobef/miniforge3/envs/drydock/bin/drydock"


def run_tui_test(prompt: str, cwd: str = ".", timeout: int = 300):
    os.chdir(cwd)

    print(f"=== TUI TEST ===")
    print(f"Prompt: {prompt[:100]}")
    print(f"CWD: {os.path.abspath(cwd)}")
    print(f"Timeout: {timeout}s")
    print(f"Binary: {DRYDOCK_BIN}")
    print()

    # Spawn the real TUI
    child = pexpect.spawn(
        DRYDOCK_BIN,
        encoding='utf-8',
        timeout=timeout,
        maxread=100000,
        env={**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"},
        cwd=cwd,
    )

    # Log everything
    log_file = open("/tmp/tui_test_raw.log", "w")
    child.logfile_read = log_file

    start = time.time()
    overwrites = 0
    api_errors = 0
    tool_calls = 0
    last_tool = ""
    write_files = {}

    try:
        # Wait for TUI to initialize (look for the input prompt)
        print("[*] Waiting for TUI to start...")
        child.expect([r'>', r'┌', r'Drydock'], timeout=30)
        time.sleep(2)  # Let it fully render

        print(f"[*] TUI started ({int(time.time()-start)}s). Sending prompt...")

        # Type the prompt character by character (Textual needs real key events)
        for char in prompt:
            child.send(char)
            time.sleep(0.01)  # Small delay between chars
        time.sleep(0.5)
        # Press Enter to submit
        child.send('\r')
        time.sleep(2)

        print(f"[*] Prompt sent. Watching for activity...")

        # Watch output in a loop
        last_activity = time.time()
        consecutive_idle = 0

        while time.time() - start < timeout:
            try:
                # Read whatever is available (non-blocking with short timeout)
                chunk = child.read_nonblocking(size=4096, timeout=10)
                if chunk:
                    last_activity = time.time()
                    consecutive_idle = 0

                    # Strip ANSI codes for analysis
                    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', chunk)
                    clean = re.sub(r'\x1b\[[0-9;]*[?][0-9]*[a-zA-Z]', '', clean)
                    clean = clean.replace('\r', '')

                    # Auto-approve: press Enter when approval prompt shows
                    if "Enter select" in clean or "always allow" in clean:
                        child.send('\r')
                        time.sleep(0.5)

                    # Detect patterns
                    if "Overwritten" in clean or "overwritten" in clean:
                        overwrites += 1
                        # Extract filename
                        m = re.search(r'(?:Overwritten|overwritten)\s+(\S+)', clean)
                        fname = m.group(1) if m else "?"
                        write_files[fname] = write_files.get(fname, 0) + 1
                        if write_files[fname] > 2:
                            print(f"  ⚠️  WRITE LOOP: {fname} overwritten {write_files[fname]} times!")

                    if "API error" in clean or "400 Bad Request" in clean:
                        api_errors += 1

                    # Detect unknown tool errors
                    if "Unknown tool" in clean:
                        tool_errors = getattr(self, '_tool_errors', 0) if hasattr(self, '_tool_errors') else 0
                        print(f"  ⚠️  UNKNOWN TOOL: {clean.strip()[:80]}")

                    # Detect search_replace errors
                    if "search_replace" in clean and "error" in clean.lower():
                        print(f"  ⚠️  SEARCH_REPLACE ERROR")

                    # Detect text repetition loop
                    if "please let me know" in clean.lower() or "please provide" in clean.lower():
                        text_loops = getattr(self, '_text_loops', 0) if hasattr(self, '_text_loops') else 0
                        text_loops += 1
                        if text_loops > 2:
                            print(f"  ⚠️  TEXT LOOP: model asking for input ({text_loops}x)")
                        print(f"  ❌ API ERROR #{api_errors}")

                    if "consecutive API errors" in clean:
                        print(f"  ❌ CONSECUTIVE API ERRORS detected")

                    # Detect tool calls
                    for tc_match in re.finditer(r'[✓✕■□]\s+(Ran |Read |Overwritten |Created |Wrote |Retrieved )', clean):
                        tool_calls += 1

                    # Detect completion
                    if "Stopping:" in clean:
                        print(f"  🛑 STOPPED: model hit error limit")
                        break

                    # Print interesting lines
                    for line in clean.split('\n'):
                        line = line.strip()
                        if not line or len(line) < 3:
                            continue
                        if any(kw in line for kw in ['✓', '✕', '■', '□', 'API error', 'LOOP', 'Stopping', '--help']):
                            elapsed = int(time.time() - start)
                            print(f"  [{elapsed:3d}s] {line[:120]}")

            except pexpect.TIMEOUT:
                consecutive_idle += 1
                elapsed = int(time.time() - start)
                if consecutive_idle > 3:
                    print(f"  [{elapsed}s] ... idle for {consecutive_idle * 10}s")
                if consecutive_idle > 12:  # 2 min idle = stuck
                    print(f"  ⚠️  STUCK: no output for 2 minutes")
                    break
                continue

            except pexpect.EOF:
                print(f"  TUI exited")
                break

    except pexpect.TIMEOUT:
        print(f"  ⏰ OVERALL TIMEOUT after {timeout}s")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
    finally:
        elapsed = int(time.time() - start)
        child.close(force=True)
        log_file.close()

    # Results
    print(f"\n{'='*60}")
    print(f"RESULTS:")
    print(f"  Duration: {elapsed}s")
    print(f"  Tool calls detected: {tool_calls}")
    print(f"  Overwrites: {overwrites}")
    print(f"  API errors: {api_errors}")
    print(f"  Files written: {dict(write_files)}")

    # Check if package was built
    import subprocess
    for d in os.listdir(cwd):
        init = os.path.join(cwd, d, "__init__.py")
        if os.path.isfile(init):
            result = subprocess.run(
                [sys.executable, "-m", d, "--help"],
                capture_output=True, text=True, timeout=10, cwd=cwd,
            )
            if result.returncode == 0:
                print(f"  --help: ✓ PASS ({d})")
            else:
                print(f"  --help: ✗ FAIL ({d}: {result.stderr[:100]})")

    issues = []
    for f, c in write_files.items():
        if c > 2:
            issues.append(f"WRITE LOOP: {f} x{c}")
    if api_errors > 0:
        issues.append(f"API ERRORS: {api_errors}")
    if elapsed >= timeout:
        issues.append("TIMEOUT")

    if issues:
        print(f"\n⚠️  ISSUES:")
        for i in issues:
            print(f"  - {i}")
    else:
        print(f"\n✅ No issues")

    return len(issues) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", help="Prompt to send to DryDock TUI")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    ok = run_tui_test(args.prompt, args.cwd, args.timeout)
    sys.exit(0 if ok else 1)
