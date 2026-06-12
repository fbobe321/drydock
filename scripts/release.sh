#!/usr/bin/env bash
# Drydock v3 release pipeline.
#
# Gates, in order — any failure aborts before anything is built or published:
#   1. ruff + pyright + pytest          (quality)
#   2. build wheel + sdist
#   3. security_scan.py on the built wheel   (provenance — HIGH blocks)
#
# It deliberately STOPS before uploading: PyPI publication is blocked on the
# account reinstatement (see docs in the v2 tree). The final step only prints
# the twine command the operator runs by hand once the account is restored.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${DRYDOCK_PY:-python3}"

echo "==> 1/3  quality gate (ruff, pyright, pytest)"
"$PY" -m ruff check drydock/ tests/
"$PY" -m pyright drydock/
"$PY" -m pytest tests/ -q --timeout=60

echo "==> 2/3  build wheel + sdist"
rm -rf dist build ./*.egg-info
"$PY" -m build

echo "==> 3/3  security scan (provenance gate)"
WHEEL="$(ls -1 dist/*.whl | head -n1)"
if [ -z "$WHEEL" ]; then
    echo "ERROR: no wheel produced in dist/" >&2
    exit 1
fi
# security_scan.py exits 2 on a HIGH finding; set -e turns that into an abort.
"$PY" scripts/security_scan.py "$WHEEL"

echo
echo "✅ Release artifacts built and scanned clean:"
ls -1 dist/
echo
echo "PUBLISH IS NOT AUTOMATED — PyPI account reinstatement is pending."
echo "Once restored, upload by hand with:"
echo "    twine upload dist/*"
