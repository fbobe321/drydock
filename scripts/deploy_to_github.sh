#!/bin/bash
# Daily deploy script for Drydock → GitHub
# Clones the GitHub repo, syncs current source, commits & pushes if changed.
#
# Usage: ./scripts/deploy_to_github.sh
# Cron:  0 4 * * * /data3/drydock/scripts/deploy_to_github.sh >> /data3/drydock/logs/deploy.log 2>&1

set -euo pipefail

DRYDOCK_SRC="/data3/drydock"
GITHUB_REPO="https://github.com/fbobe321/drydock.git"
TOKEN_FILE="$HOME/.config/drydock/github_token"
LOGDIR="$DRYDOCK_SRC/logs"
TMPDIR=""

mkdir -p "$LOGDIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cleanup() {
    if [ -n "$TMPDIR" ] && [ -d "$TMPDIR" ]; then
        rm -rf "$TMPDIR"
    fi
}
trap cleanup EXIT

# Read token
if [ ! -f "$TOKEN_FILE" ]; then
    log "ERROR: GitHub token not found at $TOKEN_FILE"
    log "Create it with: mkdir -p ~/.config/drydock && echo 'ghp_yourtoken' > ~/.config/drydock/github_token && chmod 600 ~/.config/drydock/github_token"
    exit 1
fi
GITHUB_TOKEN=$(cat "$TOKEN_FILE")

# Auth URL
AUTH_URL="https://${GITHUB_TOKEN}@github.com/fbobe321/drydock.git"

log "Running regression tests..."
cd "$DRYDOCK_SRC"
python3 -m pip install -q "pytest>=9.0" 2>/dev/null
if ! python3 -m pytest tests/test_drydock_regression.py tests/test_drydock_tasks.py tests/test_loop_detection.py tests/test_agent_tasks.py tests/test_integration.py tests/test_user_issues.py tests/test_real_issues.py \
    -p no:xdist -p no:cov --override-ini="addopts=" -q 2>&1; then
    log "TESTS FAILED — deploy aborted. Fix the tests before deploying."
    exit 1
fi
log "Tests passed."

log "Starting daily deploy..."

# Clone current GitHub state
TMPDIR=$(mktemp -d)
cd "$TMPDIR"
git clone --depth 1 "$AUTH_URL" repo 2>&1 | grep -v "^remote:" || true
cd repo

# Sync source files (exclude .git, logs, __pycache__, .github/workflows)
rsync -a --delete \
    --exclude='.git' \
    --exclude='.github/workflows' \
    --exclude='logs/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache/' \
    --exclude='*.egg-info/' \
    "$DRYDOCK_SRC/" .

# Check if anything changed
if git diff --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    log "No changes to deploy."
    exit 0
fi

# Stage all changes
git add -A

# Build commit message from recent git log in source repo
RECENT_CHANGES=$(cd "$DRYDOCK_SRC" && git log --oneline -5 2>/dev/null | head -5 || echo "manual changes")

git -c user.name="Drydock Deploy" -c user.email="deploy@drydock" \
    commit -m "$(cat <<EOF
Daily sync $(date '+%Y-%m-%d')

Recent changes:
$RECENT_CHANGES
EOF
)"

# Push
git push 2>&1 | grep -v "^remote:" || true

log "Deploy complete. $(git log --oneline -1)"
