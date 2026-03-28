#!/bin/bash
# TEST BANK — Full regression suite against real vLLM backend.
# Requires vLLM at localhost:8000. Takes 10-18 hours.
#
# Usage:
#   ./scripts/test_bank.sh           # Run everything
#   ./scripts/test_bank.sh build     # Run only build tests
#   ./scripts/test_bank.sh debug     # Run only debug tests
#   ./scripts/test_bank.sh update    # Run only update tests
#   ./scripts/test_bank.sh multi     # Run only multi-agent tests
#   ./scripts/test_bank.sh tools     # Run only tool integration tests
#   ./scripts/test_bank.sh quick     # Run only easy tests (~2 hours)

set -euo pipefail
cd "$(dirname "$0")/.."

pip install -q "pytest>=9.0" pytest-asyncio httpx 2>/dev/null

COMMON_ARGS="-v -s -p no:xdist -p no:cov --override-ini=addopts="
CATEGORY="${1:-all}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR="test_bank_results"
mkdir -p "$LOG_DIR"

run_suite() {
    local name="$1"
    local file="$2"
    local extra="${3:-}"
    echo ""
    echo "================================================================"
    echo "  $name — $(date)"
    echo "================================================================"
    local logfile="$LOG_DIR/${name}_${TIMESTAMP}.log"
    python3 -m pytest "$file" $COMMON_ARGS $extra 2>&1 | tee "$logfile"
    echo ""
    echo "Results saved to: $logfile"
}

case "$CATEGORY" in
    build)
        run_suite "BUILD" "tests/test_bank_build.py"
        ;;
    debug)
        run_suite "DEBUG" "tests/test_bank_debug.py"
        ;;
    update)
        run_suite "UPDATE" "tests/test_bank_update.py"
        ;;
    multi)
        run_suite "MULTI-AGENT" "tests/test_bank_multiagent.py"
        ;;
    tools)
        run_suite "TOOLS" "tests/test_bank_tools.py"
        ;;
    quick)
        echo "=== QUICK TEST BANK (Easy tests only, ~2 hours) ==="
        run_suite "BUILD-EASY" "tests/test_bank_build.py" "-k TestBuildEasy"
        run_suite "DEBUG-EASY" "tests/test_bank_debug.py" "-k TestDebugEasy"
        run_suite "UPDATE-EASY" "tests/test_bank_update.py" "-k TestUpdateEasy"
        run_suite "TOOLS" "tests/test_bank_tools.py"
        ;;
    all)
        echo "=== FULL TEST BANK — Estimated 10-18 hours ==="
        echo "=== Started: $(date) ==="
        run_suite "BUILD" "tests/test_bank_build.py"
        run_suite "DEBUG" "tests/test_bank_debug.py"
        run_suite "UPDATE" "tests/test_bank_update.py"
        run_suite "MULTI-AGENT" "tests/test_bank_multiagent.py"
        run_suite "TOOLS" "tests/test_bank_tools.py"
        echo ""
        echo "=== FULL TEST BANK COMPLETE — $(date) ==="
        echo "Results in: $LOG_DIR/"
        ;;
    *)
        echo "Usage: $0 {all|build|debug|update|multi|tools|quick}"
        exit 1
        ;;
esac
