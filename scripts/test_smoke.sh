#!/bin/bash
# SMOKE TESTS — Run on every code change (< 1 second)
# No backend needed. Checks imports, config, branding, safety guards.
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -q "pytest>=9.0" 2>/dev/null
python3 -m pytest tests/test_smoke.py -p no:xdist -p no:cov --override-ini="addopts=" -q "$@"
