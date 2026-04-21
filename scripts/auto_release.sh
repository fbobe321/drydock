#!/bin/bash
# Auto-release DryDock every 6 hours
# Checks if there are new commits since last release, builds, publishes, deploys
set -euo pipefail

export PATH="/home/bobef/miniconda3/bin:$PATH"
PYTHON="/home/bobef/miniconda3/bin/python3"
DRYDOCK="/data3/drydock"
LOCKFILE="$DRYDOCK/.auto_release.lock"

# Pause file: if present, skip auto-release entirely (used during manual debugging)
if [ -f "$DRYDOCK/.pause_auto_release" ]; then
    echo "[$(date)] Auto-release paused via .pause_auto_release file"
    exit 0
fi

# Prevent concurrent runs
if [ -f "$LOCKFILE" ]; then
    pid=$(cat "$LOCKFILE")
    if kill -0 "$pid" 2>/dev/null; then
        exit 0
    fi
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

cd "$DRYDOCK"

# Check if there are commits since last tag
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
COMMITS_SINCE=$(git rev-list "$LAST_TAG"..HEAD --count 2>/dev/null || echo "0")

if [ "$COMMITS_SINCE" -eq 0 ]; then
    echo "[$(date)] No new commits since $LAST_TAG. Skipping release."
    exit 0
fi

echo "[$(date)] $COMMITS_SINCE commits since $LAST_TAG. Building release..."

# Syntax check all modified .py files
ERRORS=0
for f in $(git diff --name-only "$LAST_TAG"..HEAD -- '*.py' 2>/dev/null); do
    if [ -f "$f" ]; then
        $PYTHON -c "import ast; ast.parse(open('$f').read())" 2>/dev/null || {
            echo "SYNTAX ERROR: $f"
            ERRORS=$((ERRORS + 1))
        }
    fi
done

if [ "$ERRORS" -gt 0 ]; then
    echo "[$(date)] $ERRORS syntax errors found. Aborting release."
    $PYTHON "$DRYDOCK/scripts/notify_release.py" "release" "Auto-release ABORTED: $ERRORS syntax errors in modified files" 2>/dev/null
    exit 1
fi

# Get current version. Usual path bumps PATCH; DRYDOCK_FORCE_VERSION
# overrides that (used for minor/major bumps like 2.6.x → 2.7.0 where
# PATCH+1 is the wrong arithmetic).
CURRENT=$(grep 'version = ' "$DRYDOCK/pyproject.toml" | head -1 | grep -oP '\d+\.\d+\.\d+')
if [ -n "${DRYDOCK_FORCE_VERSION:-}" ]; then
    if ! echo "$DRYDOCK_FORCE_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo "ERROR: DRYDOCK_FORCE_VERSION=$DRYDOCK_FORCE_VERSION is not N.N.N"
        exit 1
    fi
    NEW_VERSION="$DRYDOCK_FORCE_VERSION"
    echo "[$(date)] Forcing $CURRENT -> $NEW_VERSION via DRYDOCK_FORCE_VERSION"
else
    MAJOR=$(echo "$CURRENT" | cut -d. -f1)
    MINOR=$(echo "$CURRENT" | cut -d. -f2)
    PATCH=$(echo "$CURRENT" | cut -d. -f3)
    NEW_PATCH=$((PATCH + 1))
    NEW_VERSION="$MAJOR.$MINOR.$NEW_PATCH"
    echo "[$(date)] Bumping $CURRENT -> $NEW_VERSION"
fi

# Update version
sed -i "s/version = \"$CURRENT\"/version = \"$NEW_VERSION\"/" "$DRYDOCK/pyproject.toml"
git add "$DRYDOCK/pyproject.toml"
git commit -m "v$NEW_VERSION: Auto-release (${COMMITS_SINCE} commits)" --no-verify 2>/dev/null || true

# Build
rm -rf dist/
$PYTHON -m build 2>/dev/null

# Publish to PyPI
PYPI_TOKEN=$(cat ~/.config/drydock/pypi_token)
$PYTHON -m twine upload dist/drydock_cli-${NEW_VERSION}* -u __token__ -p "$PYPI_TOKEN" 2>/dev/null

# Deploy to GitHub — NOT silent. If the token is invalid this should scream.
TMPDIR=$(mktemp -d)
GITHUB_TOKEN=$(tr -d '\n' < ~/.config/drydock/github_token)
if ! curl -s -m 10 -o /dev/null -w "%{http_code}" \
        -H "Authorization: token ${GITHUB_TOKEN}" \
        https://api.github.com/user | grep -q "^200$"; then
    echo "[$(date)] ERROR: GitHub token at ~/.config/drydock/github_token is INVALID" >&2
    echo "[$(date)]   Skipping GitHub push. Rotate the token to resume deployment." >&2
    rm -rf "$TMPDIR"
else
    if git clone --depth 1 "https://${GITHUB_TOKEN}@github.com/fbobe321/drydock.git" "$TMPDIR/repo" 2>&1; then
        rsync -a --delete \
            --exclude='.git' --exclude='.github/workflows' --exclude='logs/' \
            --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' \
            --exclude='*.egg-info/' --exclude='dist/' \
            --exclude='.pause_auto_release' --exclude='log_analyzer/' \
            "$DRYDOCK/" "$TMPDIR/repo/"
        cd "$TMPDIR/repo"
        git remote set-url origin "https://${GITHUB_TOKEN}@github.com/fbobe321/drydock.git"
        git add -A
        if git -c user.name="Drydock Deploy" -c user.email="deploy@drydock" \
               commit -m "v$NEW_VERSION: Auto-release"; then
            if git push origin main; then
                echo "[$(date)] GitHub push: OK"
            else
                echo "[$(date)] ERROR: GitHub push FAILED (token may lack write scope)" >&2
            fi
        else
            echo "[$(date)] GitHub commit: no changes (already in sync)"
        fi
        rm -rf "$TMPDIR"
    else
        echo "[$(date)] ERROR: GitHub clone FAILED" >&2
        rm -rf "$TMPDIR"
    fi
fi
cd "$DRYDOCK"

# Install on user's env (retry PyPI propagation)
for i in 1 2 3; do
    sleep 60
    /home/bobef/miniforge3/envs/drydock/bin/pip install --force-reinstall --no-deps --no-cache-dir "drydock-cli==$NEW_VERSION" 2>/dev/null && break
done

# Tag
git tag "v$NEW_VERSION" 2>/dev/null || true

# Notify — include the actual change summaries since last release, not
# just a "N commits" placeholder. Pull each meaningful commit subject
# (skip Bump/Auto-release/Daily sync chatter) and the highest step from
# the most recent stress run.
COMMIT_SUMMARIES=$(git log "$LAST_TAG"..HEAD --pretty=format:'%s' 2>/dev/null \
    | grep -vE '^(Bump version|v[0-9]+\.[0-9]+\.[0-9]+:|Daily sync|Auto-release)' \
    | head -5)
if [ -z "$COMMIT_SUMMARIES" ]; then
    COMMIT_SUMMARIES="Auto-release: $COMMITS_SINCE commits since $LAST_TAG"
fi
NOTIFY_BODY="$COMMIT_SUMMARIES"

# Append previous stress run progress (matches publish_to_pypi.sh).
LAST_STRESS_LOG=$(ls -1t /tmp/stress_2000_*.log 2>/dev/null | head -1)
if [ -n "$LAST_STRESS_LOG" ]; then
    STRESS_MAX=$(grep -oE '^\[ *[0-9]+/[0-9]+\]' "$LAST_STRESS_LOG" 2>/dev/null \
        | tr -d '[] ' | awk -F/ '{print $1}' | sort -n | tail -1)
    STRESS_TOTAL=$(grep -oE '^\[ *[0-9]+/[0-9]+\]' "$LAST_STRESS_LOG" 2>/dev/null \
        | tr -d '[] ' | awk -F/ '{print $2}' | head -1)
    if [ -n "$STRESS_MAX" ] && [ -n "$STRESS_TOTAL" ]; then
        STRESS_LOG_NAME=$(basename "$LAST_STRESS_LOG")
        NOTIFY_BODY="${NOTIFY_BODY}

Previous stress run (${STRESS_LOG_NAME}) reached ${STRESS_MAX}/${STRESS_TOTAL} steps before stopping."
    fi
fi

$PYTHON "$DRYDOCK/scripts/notify_release.py" "$NEW_VERSION" "$NOTIFY_BODY" 2>/dev/null

echo "[$(date)] Released v$NEW_VERSION successfully"
