#!/bin/bash
# Start the Deep Noir activation-steering sidecar.
#
# Default port 8002. The model loads lazily on the first inference
# request, so this script returns quickly even though the actual
# Gemma 4 26B AWQ-4bit load takes ~30-60s of GPU time.
#
# Override defaults via env:
#   DRYDOCK_STEERING_SIDECAR_MODEL_PATH=/data3/Models/<other>
#   DRYDOCK_STEERING_SIDECAR_DEVICE_MAP=cuda:1   # pin to GPU 1
#   DRYDOCK_STEERING_SIDECAR_MODEL_NAME=gemma4   # /v1/models id
#
# Usage:
#   bash scripts/start_steering_sidecar.sh [--port 8002]
#
# The sidecar competes with llama.cpp for VRAM. Confirm
# `nvidia-smi` shows ≥16 GB free before starting.
set -euo pipefail

PORT=8002
HOST=0.0.0.0
PYTHON=/home/bobef/miniconda3/bin/python3

while [[ $# -gt 0 ]]; do
    case $1 in
        --port) PORT=$2; shift 2 ;;
        --host) HOST=$2; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

cd /data3/drydock

LOGFILE=/data3/drydock/logs/steering_sidecar.log
PIDFILE=/tmp/steering_sidecar.pid

if [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "steering sidecar already running (pid $(cat "$PIDFILE"))"
    exit 0
fi

echo "[$(date)] starting steering sidecar on $HOST:$PORT (log: $LOGFILE)"
nohup "$PYTHON" -m uvicorn \
    drydock.steering.sidecar.server:app \
    --host "$HOST" --port "$PORT" \
    --log-level info \
    >> "$LOGFILE" 2>&1 &

echo $! > "$PIDFILE"
echo "[$(date)] pid=$(cat "$PIDFILE")"
echo "Tail: tail -f $LOGFILE"
echo "Stop: kill \$(cat $PIDFILE)"
