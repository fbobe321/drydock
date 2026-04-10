#!/bin/bash
# vLLM failover: if .21 goes down, switch all traffic to local (.22)
# If .21 comes back up, resume using both
# Run via cron every 5 minutes

export PATH="/home/bobef/miniconda3/bin:$PATH"

LOCAL="http://localhost:8000"
REMOTE="http://192.168.50.21:8000"
STATE_FILE="/tmp/vllm_failover_state"
CONFIG="/home/bobef/.drydock/config.toml"

# Check local
LOCAL_UP=false
curl -s -m 3 "$LOCAL/v1/models" > /dev/null 2>&1 && LOCAL_UP=true

# Check remote
REMOTE_UP=false
curl -s -m 3 "$REMOTE/v1/models" > /dev/null 2>&1 && REMOTE_UP=true

# Get current state
PREV_STATE=$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")
CURR_STATE="local_only"
[ "$LOCAL_UP" = true ] && [ "$REMOTE_UP" = true ] && CURR_STATE="both_up"
[ "$LOCAL_UP" = false ] && CURR_STATE="local_down"
[ "$REMOTE_UP" = false ] && CURR_STATE="remote_down"

# Log state changes
if [ "$CURR_STATE" != "$PREV_STATE" ]; then
    echo "[$(date)] State change: $PREV_STATE → $CURR_STATE"

    case "$CURR_STATE" in
        "local_down")
            echo "[$(date)] LOCAL vLLM DOWN — restarting docker..."
            docker restart gemma4 2>/dev/null
            sleep 30
            /home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/notify_release.py "alert" \
                "LOCAL vLLM was down — restarted. Remote (.21) is ${REMOTE_UP}" 2>/dev/null
            ;;
        "remote_down")
            echo "[$(date)] REMOTE vLLM (.21) DOWN — using local only"
            /home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/notify_release.py "alert" \
                "Remote vLLM (.21) is DOWN. All processing on local server." 2>/dev/null
            ;;
        "both_up")
            echo "[$(date)] Both servers UP"
            /home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/notify_release.py "status" \
                "Both vLLM servers UP (local + .21)" 2>/dev/null
            ;;
    esac
fi

echo "$CURR_STATE" > "$STATE_FILE"
