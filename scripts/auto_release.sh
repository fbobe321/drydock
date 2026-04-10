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

# Get current version and bump patch
CURRENT=$(grep 'version = ' "$DRYDOCK/pyproject.toml" | head -1 | grep -oP '\d+\.\d+\.\d+')
MAJOR=$(echo "$CURRENT" | cut -d. -f1)
MINOR=$(echo "$CURRENT" | cut -d. -f2)
PATCH=$(echo "$CURRENT" | cut -d. -f3)
NEW_PATCH=$((PATCH + 1))
NEW_VERSION="$MAJOR.$MINOR.$NEW_PATCH"

echo "[$(date)] Bumping $CURRENT -> $NEW_VERSION"

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

# Notify
$PYTHON "$DRYDOCK/scripts/notify_release.py" "$NEW_VERSION" "Auto-release: $COMMITS_SINCE commits since $LAST_TAG" 2>/dev/null

echo "[$(date)] Released v$NEW_VERSION successfully"
