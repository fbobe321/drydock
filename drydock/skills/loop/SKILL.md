---
name: loop
description: Run a prompt repeatedly on an interval. Useful for monitoring, polling, or iterative tasks.
allowed-tools: bash read_file grep glob search_replace write_file
user-invocable: true
---

# Loop Execution

Run the given task repeatedly. Usage: `/loop [interval_seconds] <task>`

Default interval: 60 seconds.

1. Parse interval from $0 (if numeric), otherwise default to 60
2. Execute the task: $ARGUMENTS
3. Wait for the interval
4. Repeat until interrupted or task reports completion

## Common uses:
- `/loop 30 check if the build passed` — poll CI every 30s
- `/loop 60 run tests and fix failures` — continuous test-fix loop
- `/loop 120 check PR comments and respond` — monitor PR
