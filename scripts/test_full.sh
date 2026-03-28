#!/bin/bash
# FULL REGRESSION — Run nightly or before releases (5-10 min)
# Requires vLLM at localhost:8000. Real backend, no mocks.
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -q "pytest>=9.0" pytest-asyncio 2>/dev/null

echo "=== SMOKE TESTS ==="
python3 -m pytest tests/test_smoke.py -p no:xdist -p no:cov --override-ini="addopts=" -q

echo ""
echo "=== FULL REGRESSION (real backend) ==="
python3 -m pytest tests/test_full_regression.py -v -p no:xdist -p no:cov --override-ini="addopts="
