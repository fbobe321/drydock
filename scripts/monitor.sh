#!/bin/bash
# Monitor Gemma 4 Docker container and DryDock v3
# Restart if down, notify via Telegram

export PATH="/home/bobef/miniconda3/bin:$PATH"
PYTHON="/home/bobef/miniconda3/bin/python3"

# Check if Gemma 4 is serving
if ! curl -s -m 5 http://localhost:8000/v1/models > /dev/null 2>&1; then
    echo "[$(date)] Gemma 4 is DOWN — restarting"
    bash /data3/Models/start_gemma4.sh
    sleep 120
    if curl -s -m 5 http://localhost:8000/v1/models > /dev/null 2>&1; then
        $PYTHON /data3/drydock/scripts/notify_release.py "monitor" "Gemma 4 was down, restarted successfully."
    else
        $PYTHON /data3/drydock/scripts/notify_release.py "monitor" "Gemma 4 is DOWN and restart FAILED. Check manually."
    fi
fi
