#!/bin/bash
# Publish drydock-cli to PyPI.
# Runs regression tests, bumps version, builds, and uploads.
#
# Usage: ./scripts/publish_to_pypi.sh [version]
#   version: e.g. "0.5.0" (if omitted, bumps patch from current)

set -euo pipefail

DRYDOCK_SRC="/data3/drydock"
TOKEN_FILE="$HOME/.config/drydock/pypi_token"
PYTHON="/home/bobef/miniconda3/bin/python3"
export PATH="/home/bobef/miniconda3/bin:$PATH"

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
$PYTHON -m pip install -q "pytest>=9.0" 2>/dev/null
if ! $PYTHON -m pytest tests/test_drydock_regression.py tests/test_drydock_tasks.py tests/test_loop_detection.py tests/test_agent_tasks.py tests/test_integration.py tests/test_user_issues.py tests/test_real_issues.py \
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
# Pass NEW_VERSION so deploy_to_github.sh can tag the synthetic sync
# commit — local tags can't be pushed because our rsync + Daily-sync
# approach means local and remote histories are disjoint.
if [ -x "$DRYDOCK_SRC/scripts/deploy_to_github.sh" ]; then
    log "Deploying to GitHub..."
    "$DRYDOCK_SRC/scripts/deploy_to_github.sh" "$NEW_VERSION" 2>&1 || log "GitHub deploy failed (non-fatal)"
fi

# --- Git tag ---
git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION" 2>/dev/null || true
log "Tagged v$NEW_VERSION"

# --- Quick integration test (install in temp venv and verify) ---
log "Running integration test..."
TMPVENV=$(mktemp -d)
$PYTHON -m venv "$TMPVENV/venv" 2>/dev/null
if "$TMPVENV/venv/bin/pip" install -q "dist/drydock_cli-${NEW_VERSION}-py3-none-any.whl" 2>/dev/null; then
    INSTALLED_VER=$("$TMPVENV/venv/bin/python3" -c "from importlib.metadata import version; print(version('drydock-cli'))" 2>/dev/null || echo "unknown")
    if [ "$INSTALLED_VER" = "$NEW_VERSION" ]; then
        log "Integration test PASSED: installed version $INSTALLED_VER matches"
    else
        log "WARNING: version mismatch — expected $NEW_VERSION, got $INSTALLED_VER"
    fi
else
    log "WARNING: integration test failed to install (non-fatal)"
fi
rm -rf "$TMPVENV"

# --- Send Telegram notification ---
# Pull the most recent meaningful commit (skip version-bump and auto-release
# commits so the summary actually describes what changed).
COMMIT_MSG=$(git log --oneline -n 10 --pretty=format:'%s' 2>/dev/null \
    | grep -vE '^(Bump version|v[0-9]+\.[0-9]+\.[0-9]+:|Daily sync|Auto-release)' \
    | head -1)
if [ -z "$COMMIT_MSG" ]; then
    COMMIT_MSG=$(git log --oneline -1 2>/dev/null | cut -d' ' -f2-)
fi

# Append highest step reached by the most recent 2000-step stress run.
# Helps the release note show progress: "last run got to 42/1658 before
# the regression — this fix should push past it."
LAST_STRESS_LOG=$(ls -1t /tmp/stress_2000_*.log 2>/dev/null | head -1)
if [ -n "$LAST_STRESS_LOG" ]; then
    STRESS_MAX=$(grep -oE '^\[ *[0-9]+/[0-9]+\]' "$LAST_STRESS_LOG" 2>/dev/null \
        | tr -d '[] ' | awk -F/ '{print $1}' | sort -n | tail -1)
    STRESS_TOTAL=$(grep -oE '^\[ *[0-9]+/[0-9]+\]' "$LAST_STRESS_LOG" 2>/dev/null \
        | tr -d '[] ' | awk -F/ '{print $2}' | head -1)
    if [ -n "$STRESS_MAX" ] && [ -n "$STRESS_TOTAL" ]; then
        STRESS_LOG_NAME=$(basename "$LAST_STRESS_LOG")
        COMMIT_MSG="${COMMIT_MSG}

Previous stress run (${STRESS_LOG_NAME}) reached ${STRESS_MAX}/${STRESS_TOTAL} steps before stopping."
    fi
fi

$PYTHON "$DRYDOCK_SRC/scripts/notify_release.py" "$NEW_VERSION" "$COMMIT_MSG" 2>/dev/null || true

log "Published drydock-cli $NEW_VERSION to PyPI"
