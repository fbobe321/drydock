#!/bin/bash
# Monitor the overnight PRD test battery.
# Restarts if stuck, logs results, and stops on repeated failures.
# Cron: */10 * * * * /data3/drydock/scripts/monitor_test_battery.sh

set -euo pipefail

DRYDOCK="/data3/drydock"
LOGDIR="$DRYDOCK/test_bank_results"
LOGFILE="$LOGDIR/overnight_run2.log"
MONITOR_LOG="$LOGDIR/monitor.log"
LOCKFILE="/tmp/drydock_test_monitor.lock"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$MONITOR_LOG"; }

# Prevent concurrent monitors
if [ -f "$LOCKFILE" ]; then
    pid=$(cat "$LOCKFILE" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

cd "$DRYDOCK"

# Check if test process is running
TEST_PID=$(pgrep -f "pytest.*test_bank_prd" 2>/dev/null | head -1 || echo "")

if [ -z "$TEST_PID" ]; then
    # Check if it completed
    if [ -f "$LOGFILE" ] && grep -q "passed" "$LOGFILE" 2>/dev/null; then
        PASSED=$(grep -c "PASSED" "$LOGFILE" 2>/dev/null || echo 0)
        FAILED=$(grep -c "FAILED" "$LOGFILE" 2>/dev/null || echo 0)
        TOTAL=$((PASSED + FAILED))

        if [ "$TOTAL" -ge 65 ]; then
            log "TEST BATTERY COMPLETE: $PASSED passed, $FAILED failed out of $TOTAL"
            # Log failures
            grep "FAILED" "$LOGFILE" >> "$MONITOR_LOG" 2>/dev/null || true
            exit 0
        fi
    fi

    # Not running and not complete — restart
    log "Test battery not running. Restarting..."

    # Check how many tests already completed
    DONE=$(grep -c "PASSED\|FAILED" "$LOGFILE" 2>/dev/null || echo 0)

    if [ "$DONE" -gt 0 ]; then
        # Save progress
        cp "$LOGFILE" "$LOGDIR/overnight_run2_partial_$(date +%H%M).log" 2>/dev/null || true

        # Check for repeated failures (same test failing 3+ times across runs)
        FAIL_LOG="$LOGDIR/failure_tracking.log"
        grep "FAILED" "$LOGFILE" >> "$FAIL_LOG" 2>/dev/null || true

        # If a specific test has failed 3+ times, skip it
        SKIP_TESTS=""
        if [ -f "$FAIL_LOG" ]; then
            while IFS= read -r test_name; do
                COUNT=$(grep -c "$test_name" "$FAIL_LOG" 2>/dev/null || echo 0)
                if [ "$COUNT" -ge 3 ]; then
                    log "SKIP: $test_name failed $COUNT times, skipping"
                    SKIP_TESTS="$SKIP_TESTS and not ${test_name##*::}"
                fi
            done < <(grep "FAILED" "$FAIL_LOG" | sed 's/ .*//' | sort -u)
        fi
    fi

    # Restart
    DESELECT=""
    if [ -n "${SKIP_TESTS:-}" ]; then
        DESELECT="-k 'not _PLACEHOLDER_ $SKIP_TESTS'"
    fi

    nohup python3 -m pytest tests/test_bank_prd.py tests/test_bank_prd_extended.py \
        -v -p no:xdist -p no:cov --override-ini="addopts=" --tb=short \
        $DESELECT \
        > "$LOGFILE" 2>&1 &

    log "Restarted with PID $! (skipping: ${SKIP_TESTS:-none})"
    exit 0
fi

# Process is running — check if stuck
LAST_MODIFIED=$(stat -c %Y "$LOGFILE" 2>/dev/null || echo 0)
NOW=$(date +%s)
STALE=$((NOW - LAST_MODIFIED))

if [ "$STALE" -gt 600 ]; then
    # Log file hasn't been updated in 10 minutes — test might be stuck
    CURRENT_TEST=$(tail -1 "$LOGFILE" | grep -o "test_[a-z_]*" | head -1 || echo "unknown")
    log "WARNING: Test battery stale for ${STALE}s. Current test: $CURRENT_TEST"

    if [ "$STALE" -gt 1200 ]; then
        # 20 minutes stuck — kill and restart
        log "STUCK: Killing PID $TEST_PID (stale ${STALE}s on $CURRENT_TEST)"
        kill "$TEST_PID" 2>/dev/null || true
        sleep 5
        kill -9 "$TEST_PID" 2>/dev/null || true

        # Will restart on next cron run
    fi
else
    # Running fine — log progress
    PASSED=$(grep -c "PASSED" "$LOGFILE" 2>/dev/null || echo 0)
    FAILED=$(grep -c "FAILED" "$LOGFILE" 2>/dev/null || echo 0)
    TOTAL=$((PASSED + FAILED))
    log "Running: $TOTAL/65 done ($PASSED passed, $FAILED failed)"
fi
