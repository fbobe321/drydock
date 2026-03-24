#!/bin/bash
# Publish drydock-cli to PyPI.
# Runs regression tests, bumps version, builds, and uploads.
#
# Usage: ./scripts/publish_to_pypi.sh [version]
#   version: e.g. "0.5.0" (if omitted, bumps patch from current)

set -euo pipefail

DRYDOCK_SRC="/data3/drydock"
TOKEN_FILE="$HOME/.config/drydock/pypi_token"
PYTHON="python3"

cd "$DRYDOCK_SRC"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- Check token ---
if [ ! -f "$TOKEN_FILE" ]; then
    echo "ERROR: PyPI token not found at $TOKEN_FILE"
    echo "Create it with: echo 'pypi-yourtoken' > $TOKEN_FILE && chmod 600 $TOKEN_FILE"
    exit 1
fi
PYPI_TOKEN=$(cat "$TOKEN_FILE")

# --- Run tests ---
log "Running regression tests..."
if ! $PYTHON -m pytest tests/test_drydock_regression.py tests/test_drydock_tasks.py \
    -p no:xdist -p no:cov --override-ini="addopts=" -q 2>&1; then
    log "TESTS FAILED — publish aborted."
    exit 1
fi
log "All tests passed."

# --- Determine version ---
CURRENT=$($PYTHON -c "import tomli; print(tomli.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null || \
          grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
log "Current version: $CURRENT"

if [ -n "${1:-}" ]; then
    NEW_VERSION="$1"
else
    # Auto-bump patch: 0.4.0 → 0.4.1
    IFS='.' read -r major minor patch <<< "$CURRENT"
    NEW_VERSION="$major.$minor.$((patch + 1))"
fi
log "New version: $NEW_VERSION"

# --- Update version in pyproject.toml ---
sed -i "s/^version = \"$CURRENT\"/version = \"$NEW_VERSION\"/" pyproject.toml

# --- Build ---
log "Building..."
rm -rf dist/
$PYTHON -m build --wheel --sdist 2>&1 | tail -2

# --- Upload ---
log "Publishing to PyPI..."
uv publish dist/drydock_cli-${NEW_VERSION}* --token "$PYPI_TOKEN" 2>&1

# --- Commit version bump ---
git add pyproject.toml
git commit -m "Bump version to $NEW_VERSION for PyPI release

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>" 2>/dev/null || true

# --- Deploy to GitHub too ---
if [ -x "$DRYDOCK_SRC/scripts/deploy_to_github.sh" ]; then
    log "Deploying to GitHub..."
    "$DRYDOCK_SRC/scripts/deploy_to_github.sh" 2>&1 || log "GitHub deploy failed (non-fatal)"
fi

log "Published drydock-cli $NEW_VERSION to PyPI"
