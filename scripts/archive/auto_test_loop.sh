#!/bin/bash
# Automated DryDock testing loop.
# Runs DryDock on PRDs, checks results, logs everything.
# Designed to run for 20+ hours unattended.
#
# Usage: nohup ./scripts/auto_test_loop.sh > test_bank_results/auto_loop.log 2>&1 &

set -uo pipefail

export PATH="/home/bobef/miniconda3/bin:$PATH"
PYTHON="/home/bobef/miniconda3/bin/python3"
DRYDOCK="/data3/drydock"
RESULTS="$DRYDOCK/test_bank_results"
LOG="$RESULTS/auto_loop.log"
MAX_HOURS=20
START=$(date +%s)

mkdir -p "$RESULTS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Check if we've exceeded time limit
check_time() {
    NOW=$(date +%s)
    ELAPSED=$(( (NOW - START) / 3600 ))
    if [ "$ELAPSED" -ge "$MAX_HOURS" ]; then
        log "Time limit reached ($ELAPSED hours). Stopping."
        exit 0
    fi
}

# Run DryDock on a PRD and check the result
run_prd_test() {
    local prd_file="$1"
    local test_dir="$2"
    local test_name="$3"

    log "=== Testing: $test_name ==="

    # Clean
    rm -rf "$test_dir"
    mkdir -p "$test_dir"
    cp "$prd_file" "$test_dir/PRD.md"

    # Run DryDock programmatically
    cd "$test_dir"
    local result_file="$RESULTS/${test_name}_$(date +%H%M%S).txt"

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
    agent = make_agent(Path('$test_dir'), max_turns=30)
    r = await run_workload(agent, max_events=300, prompt='Review the PRD and build the project. Test it.')
    n = count_python_files(Path('$test_dir'))
    errs = check_syntax_all(Path('$test_dir'))
    print(f'RESULT: files={n} syntax_errors={len(errs)} tools={r.total_tool_calls} stops={r.force_stops}')
    print(f'TOOLS: {r.summary()}')
    # Try to run the project
    for d in Path('$test_dir').iterdir():
        if d.is_dir() and (d / '__init__.py').exists():
            result = subprocess.run(f'python3 -m {d.name} --help', shell=True, capture_output=True, text=True, timeout=10)
            print(f'RUN_HELP: rc={result.returncode}')
            if result.returncode == 0:
                print(f'HELP: {result.stdout[:200]}')
            break
    else:
        print('RUN_HELP: no_package')

asyncio.run(test())
" > "$result_file" 2>&1

    local rc=$?

    # Check results
    local files=$(grep "RESULT:" "$result_file" 2>/dev/null | grep -o "files=[0-9]*" | cut -d= -f2)
    local help_rc=$(grep "RUN_HELP:" "$result_file" 2>/dev/null | grep -o "rc=[0-9]*" | cut -d= -f2)

    if [ "$rc" -ne 0 ]; then
        log "  TIMEOUT or CRASH (rc=$rc)"
        echo "FAIL" > "$result_file.status"
        return 1
    elif [ "${help_rc:-1}" = "0" ]; then
        log "  PASS: $files files, --help works"
        echo "PASS" > "$result_file.status"
        return 0
    elif [ "${files:-0}" -gt "2" ]; then
        log "  PARTIAL: $files files created but --help failed"
        echo "PARTIAL" > "$result_file.status"
        return 1
    else
        log "  FAIL: ${files:-0} files"
        echo "FAIL" > "$result_file.status"
        return 1
    fi
}

# ============================================================================
# PRD Collection
# ============================================================================

# Create PRDs if they don't exist
PRD_DIR="$RESULTS/prds"
mkdir -p "$PRD_DIR"

# PRD 1: Log Analyzer (the user's main test)
if [ -f "/data3/test_drydock/PRD.md" ]; then
    cp /data3/test_drydock/PRD.md "$PRD_DIR/log_analyzer.md"
fi

# PRD 2: Simple Todo App
cat > "$PRD_DIR/todo_app.md" << 'PRDEOF'
# Todo CLI App

**IMPORTANT:** Do NOT run git commands. Only work within this directory.

## Overview
A command-line todo list manager with JSON persistence.

## Commands
```
python3 -m todo add "Buy groceries"
python3 -m todo list
python3 -m todo done 1
python3 -m todo remove 1
```

## Structure
- todo/ package with __init__.py, __main__.py, cli.py, store.py

## Requirements
- Python stdlib only (json, argparse)
- Store todos in todos.json
PRDEOF

# PRD 3: Password Generator
cat > "$PRD_DIR/password_gen.md" << 'PRDEOF'
# Password Generator

**IMPORTANT:** Do NOT run git commands. Only work within this directory.

## Overview
Generate secure random passwords from the command line.

## Usage
```
python3 -m passgen --length 16 --count 3
```

## Structure
- passgen/ package with __init__.py, __main__.py, cli.py, generator.py

## Requirements
- Python stdlib only (secrets, string, argparse)
PRDEOF

# PRD 4: CSV Converter
cat > "$PRD_DIR/csv_converter.md" << 'PRDEOF'
# CSV Converter

**IMPORTANT:** Do NOT run git commands. Only work within this directory.

## Overview
Convert CSV files to JSON and back.

## Usage
```
python3 -m csvtool to-json input.csv -o output.json
python3 -m csvtool stats input.csv
```

## Structure
- csvtool/ package with __init__.py, __main__.py, cli.py, converter.py

## Requirements
- Python stdlib only (csv, json, argparse)
PRDEOF

# PRD 5: File Organizer
cat > "$PRD_DIR/file_organizer.md" << 'PRDEOF'
# File Organizer

**IMPORTANT:** Do NOT run git commands. Only work within this directory.

## Overview
Organize files in a directory by type into subdirectories.

## Usage
```
python3 -m organizer /path/to/dir --dry-run
```

## Rules
- .py → code/
- .jpg, .png → images/
- .pdf, .txt → documents/
- Everything else → other/

## Structure
- organizer/ package with __init__.py, __main__.py, cli.py, organizer.py

## Requirements
- Python stdlib only (shutil, pathlib, argparse)
PRDEOF

# PRD 6: Key-Value Store
cat > "$PRD_DIR/kv_store.md" << 'PRDEOF'
# Key-Value Store CLI

**IMPORTANT:** Do NOT run git commands. Only work within this directory.

## Overview
A persistent key-value store with CLI interface.

## Commands
```
python3 -m kvstore set mykey "my value"
python3 -m kvstore get mykey
python3 -m kvstore list
python3 -m kvstore delete mykey
```

## Structure
- kvstore/ package with __init__.py, __main__.py, cli.py, store.py

## Requirements
- Python stdlib only (json, argparse)
- Store data in store.json
PRDEOF

# ============================================================================
# Main Loop
# ============================================================================

log "Starting auto test loop (max $MAX_HOURS hours)"
log "PRDs: $(ls $PRD_DIR/*.md | wc -l)"

PASS=0
FAIL=0
PARTIAL=0
ROUND=0

while true; do
    check_time
    ROUND=$((ROUND + 1))
    log ""
    log "========== ROUND $ROUND =========="

    for prd in "$PRD_DIR"/*.md; do
        check_time
        name=$(basename "$prd" .md)
        test_dir="/tmp/drydock_auto_test_${name}"

        if run_prd_test "$prd" "$test_dir" "$name"; then
            PASS=$((PASS + 1))
        else
            # Check if partial
            status=$(cat "$RESULTS/${name}_*.status" 2>/dev/null | tail -1)
            if [ "$status" = "PARTIAL" ]; then
                PARTIAL=$((PARTIAL + 1))
            else
                FAIL=$((FAIL + 1))
            fi
        fi

        log "  Score: $PASS pass, $PARTIAL partial, $FAIL fail"
    done

    log ""
    log "Round $ROUND complete: $PASS pass, $PARTIAL partial, $FAIL fail"
    log ""

    # After first round, if >50% pass, continue. Otherwise, wait and retry.
    TOTAL=$((PASS + PARTIAL + FAIL))
    if [ "$TOTAL" -gt 0 ]; then
        PASS_RATE=$((PASS * 100 / TOTAL))
        log "Pass rate: $PASS_RATE% ($PASS/$TOTAL)"
    fi

    # Brief pause between rounds
    sleep 30
done
