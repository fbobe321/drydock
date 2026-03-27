#!/bin/bash
# Backup DryDock project to NAS
# Cron: 0 3 * * * /data3/drydock/scripts/backup.sh >> /data3/drydock/logs/backup.log 2>&1

set -euo pipefail

DRYDOCK_SRC="/data3/drydock"
BENCH_DIR="/data3/swe_bench_runs"
NAS_MOUNT="/mnt/nas_backups"
BACKUP_DIR="$NAS_MOUNT/drydock"
DATE=$(date +%Y%m%d)
LOGDIR="$DRYDOCK_SRC/logs"

mkdir -p "$LOGDIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Check NAS is mounted
if ! mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
    log "NAS not mounted. Attempting mount..."
    sudo mount "$NAS_MOUNT" 2>/dev/null || {
        log "ERROR: Cannot mount NAS at $NAS_MOUNT. Skipping backup."
        exit 1
    }
fi

mkdir -p "$BACKUP_DIR"

log "Starting backup..."

# Backup 1: DryDock source code (excluding __pycache__, .git objects)
rsync -rlpt --delete --no-group --no-owner \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    --exclude='dist/' \
    --exclude='*.egg-info/' \
    --exclude='.git/objects/' \
    --exclude='logs/' \
    "$DRYDOCK_SRC/" "$BACKUP_DIR/source/"

log "Source backed up."

# Backup 2: SWE-bench results and state (not the repos — too large)
rsync -rlpt --delete --no-group --no-owner \
    --exclude='repos/' \
    --exclude='tasks/*/repo/' \
    --exclude='__pycache__/' \
    "$BENCH_DIR/continuous_bench_state.json" \
    "$BENCH_DIR/auto_improve_state.json" \
    "$BENCH_DIR/harness.py" \
    "$BENCH_DIR/continuous_bench.sh" \
    "$BENCH_DIR/analyze_batch.py" \
    "$BACKUP_DIR/bench/" 2>/dev/null || true

# Backup results directories (small — just JSON)
rsync -rlpt --no-group --no-owner \
    --include='*/' \
    --include='results.json' \
    --exclude='*' \
    "$BENCH_DIR/results/" "$BACKUP_DIR/bench/results/" 2>/dev/null || true

log "Bench state backed up."

# Backup 3: Config files
mkdir -p "$BACKUP_DIR/config"
cp -a ~/.config/drydock/ "$BACKUP_DIR/config/drydock_config/" 2>/dev/null || true
cp -a ~/.drydock/ "$BACKUP_DIR/config/drydock_home/" 2>/dev/null || true

log "Config backed up."

# Backup 4: Crontab
crontab -l > "$BACKUP_DIR/crontab_$(hostname).txt" 2>/dev/null || true

# Write backup manifest
cat > "$BACKUP_DIR/MANIFEST.txt" << EOF
DryDock Backup
Date: $(date)
Host: $(hostname)
Source: $DRYDOCK_SRC
Bench: $BENCH_DIR
Version: $(python3 -c "import sys; sys.path.insert(0,'$DRYDOCK_SRC'); from drydock import __version__; print(__version__)" 2>/dev/null || echo "unknown")
EOF

log "Backup complete to $BACKUP_DIR"
