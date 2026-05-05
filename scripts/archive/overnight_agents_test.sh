#!/bin/bash
# Overnight test: Try different AGENTS.md versions with plan→build workflow
# Tests DryDock's ability to build the log analyzer from PRD
# Runs for up to 20 hours, logs all results

set -uo pipefail
export PATH="/home/bobef/miniconda3/bin:$PATH"
PYTHON="/home/bobef/miniconda3/bin/python3"
DRYDOCK="/data3/drydock"
RESULTS="$DRYDOCK/test_bank_results/agents_md_test"
PRD="/data3/test_drydock/PRD.md"
MAX_HOURS=20
START=$(date +%s)

mkdir -p "$RESULTS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$RESULTS/run.log"; }

check_time() {
    NOW=$(date +%s)
    ELAPSED=$(( (NOW - START) / 3600 ))
    if [ "$ELAPSED" -ge "$MAX_HOURS" ]; then
        log "Time limit reached ($ELAPSED hours). Stopping."
        $PYTHON "$DRYDOCK/scripts/notify_release.py" "test" "Overnight AGENTS.md test complete. $PASS passes, $FAIL fails. Check results."
        exit 0
    fi
}

# Test a single AGENTS.md variant
run_test() {
    local variant_name="$1"
    local agents_md_content="$2"
    local test_dir="/tmp/drydock_agentsmd_test_$$"
    local result_file="$RESULTS/${variant_name}_$(date +%H%M%S).txt"

    log "=== Testing variant: $variant_name ==="

    rm -rf "$test_dir"
    mkdir -p "$test_dir"
    cp "$PRD" "$test_dir/PRD.md"
    echo "$agents_md_content" > "$test_dir/AGENTS.md"

    cd "$test_dir"

    # Run DryDock programmatically: simulate plan→build
    timeout 600 $PYTHON -c "
import asyncio, os, sys, subprocess
from pathlib import Path
sys.path.insert(0, '$DRYDOCK')
sys.path.insert(0, '$DRYDOCK/tests')
os.chdir('$test_dir')
from drydock.core.config.harness_files import init_harness_files_manager
try: init_harness_files_manager('u','p')
except: pass
from testbank_helpers import make_agent, run_workload, count_python_files, check_syntax_all

async def test():
    agent = make_agent(Path('$test_dir'), max_turns=40)
    r = await run_workload(agent, max_events=400, prompt='review the PRD and build the log analyzer. Test it.')
    n = count_python_files(Path('$test_dir'))
    errs = check_syntax_all(Path('$test_dir'))
    print(f'RESULT: files={n} syntax_errors={len(errs)} tools={r.total_tool_calls} stops={r.force_stops}')
    print(f'TOOLS: {r.summary()}')
    # Check subagent usage
    task_calls = r.tool_counts.get('task', 0)
    print(f'SUBAGENT_CALLS: {task_calls}')
    # Try to run
    for d in Path('$test_dir').iterdir():
        if d.is_dir() and (d / '__init__.py').exists():
            result = subprocess.run(f'python3 -m {d.name} --help', shell=True, capture_output=True, text=True, timeout=10)
            print(f'HELP: rc={result.returncode}')
            if result.returncode == 0:
                print(f'HELP_OUT: {result.stdout[:200]}')
            # Try analyze
            Path('$test_dir/test.log').write_text('2026-03-01 10:01:22 ERROR DBConnection failed: timeout\n2026-03-01 10:01:23 WARN Retrying\n2026-03-01 10:02:00 INFO Recovered\n')
            result2 = subprocess.run(f'python3 -m {d.name} analyze test.log', shell=True, capture_output=True, text=True, timeout=10)
            print(f'ANALYZE: rc={result2.returncode}')
            if result2.returncode == 0:
                print(f'ANALYZE_OUT: {result2.stdout[:300]}')
            break
    else:
        print('HELP: no_package')

asyncio.run(test())
" > "$result_file" 2>&1

    local rc=$?

    # Parse results
    local files=$(grep "RESULT:" "$result_file" 2>/dev/null | grep -o "files=[0-9]*" | cut -d= -f2)
    local help_rc=$(grep "HELP:" "$result_file" 2>/dev/null | grep -o "rc=[0-9]*" | cut -d= -f2)
    local analyze_rc=$(grep "ANALYZE:" "$result_file" 2>/dev/null | grep -o "rc=[0-9]*" | cut -d= -f2)
    local subagent=$(grep "SUBAGENT_CALLS:" "$result_file" 2>/dev/null | grep -o "[0-9]*")

    if [ "${analyze_rc:-1}" = "0" ]; then
        log "  FULL PASS: $files files, analyze works, subagents=$subagent"
        echo "FULL_PASS" > "$result_file.status"
        return 0
    elif [ "${help_rc:-1}" = "0" ]; then
        log "  HELP PASS: $files files, --help works, subagents=$subagent"
        echo "HELP_PASS" > "$result_file.status"
        return 1
    elif [ "${files:-0}" -gt "3" ]; then
        log "  PARTIAL: $files files, subagents=$subagent"
        echo "PARTIAL" > "$result_file.status"
        return 1
    else
        log "  FAIL: ${files:-0} files, subagents=${subagent:-0}"
        echo "FAIL" > "$result_file.status"
        return 1
    fi
}

# ============================================================================
# AGENTS.md Variants
# ============================================================================

VARIANT_A='# Project Instructions

## Workflow
When building a project:
1. Use `task(agent="planner")` to create an implementation plan first
2. Use `task(agent="explore")` to understand existing code
3. Create files using `write_file` with absolute imports
4. Always create `__main__.py` so `python3 -m package_name` works
5. Test with `bash` after creating files

## Rules
- Use absolute imports: `from package.module import X`, NOT `from .module import X`
- Always create `__init__.py` and `__main__.py` for packages
- Create sample test data before testing
- Use `python3 -m package_name` to run, NOT `python3 package/file.py`'

VARIANT_B='# Project Instructions

## Build Workflow
1. Read the PRD with `read_file`
2. Plan: use `task(agent="planner")` to create an implementation plan
3. Create each file with `write_file` using absolute imports
4. Create `__init__.py` and `__main__.py` for every package
5. Create sample data and test with `bash`

## Import Rules
- ALWAYS: `from package.module import X`
- NEVER: `from .module import X`
- Run with: `python3 -m package_name`'

VARIANT_C='# Instructions for AI Agent

When asked to build a project:
- Start by reading any PRD or requirements files
- Use task(agent="planner") to plan the implementation
- Create files one at a time with write_file
- Use absolute imports (from pkg.mod import X, not from .mod import X)
- Create __init__.py and __main__.py for packages
- Test with python3 -m package_name after building'

VARIANT_D='# Build Rules

1. Read requirements (PRD.md)
2. Plan with task(agent="planner")
3. Explore existing code with task(agent="explore")
4. Create files with write_file
5. Use absolute imports only
6. Always create __init__.py and __main__.py
7. Test with python3 -m package_name
8. Fix errors with search_replace'

VARIANT_E='# Project Configuration

## Agent Workflow
- For new projects: task(agent="planner") → write_file → bash test
- For existing projects: task(agent="explore") → read_file → search_replace
- For reviews: task(agent="explore") → invoke_skill(skill_name="review")

## Package Rules
- Absolute imports: from package.module import X
- Always: __init__.py + __main__.py
- Test: python3 -m package_name
- Never: from .module import X'

# ============================================================================
# Main Loop
# ============================================================================

log "Starting overnight AGENTS.md test (max $MAX_HOURS hours)"
log "Variants: A B C D E"

PASS=0
FAIL=0
ROUND=0

while true; do
    check_time
    ROUND=$((ROUND + 1))
    log ""
    log "========== ROUND $ROUND =========="

    for variant in A B C D E; do
        check_time
        eval "content=\$VARIANT_$variant"
        if run_test "variant_$variant" "$content"; then
            PASS=$((PASS + 1))
        else
            FAIL=$((FAIL + 1))
        fi
    done

    TOTAL=$((PASS + FAIL))
    PASS_RATE=$((PASS * 100 / TOTAL))
    log "Round $ROUND: $PASS/$TOTAL pass ($PASS_RATE%)"

    # Send Telegram update every 5 rounds
    if [ $((ROUND % 5)) -eq 0 ]; then
        $PYTHON "$DRYDOCK/scripts/notify_release.py" "test-r$ROUND" "AGENTS.md test round $ROUND: $PASS/$TOTAL pass ($PASS_RATE%). Best variant TBD."
    fi

    sleep 30
done
