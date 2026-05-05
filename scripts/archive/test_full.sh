#!/bin/bash
# FULL REGRESSION — Run nightly or before releases (5-10 min)
# Requires vLLM at localhost:8000. Real backend, no mocks.
set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="/home/bobef/miniconda3/bin:$PATH"
PYTHON="/home/bobef/miniconda3/bin/python3"

$PYTHON -m pip install -q "pytest>=9.0" pytest-asyncio 2>/dev/null

VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
echo "Drydock v$VERSION — $(date)"
echo "Python: $($PYTHON --version)"
echo ""

echo "=== SMOKE TESTS ==="
$PYTHON -m pytest tests/test_smoke.py -p no:xdist -p no:cov --override-ini="addopts=" -q

echo ""
echo "=== FULL REGRESSION (real backend) ==="
$PYTHON -m pytest tests/test_full_regression.py -v -p no:xdist -p no:cov --override-ini="addopts="

echo ""
echo "=== BUILD PROJECT TESTS (real backend, 30-60 min) ==="
$PYTHON -m pytest tests/test_build_projects.py -v -s -p no:xdist -p no:cov --override-ini="addopts="
