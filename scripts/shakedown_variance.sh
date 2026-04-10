#!/bin/bash
# Run the user-pain suite N times and report pass rate per project.
# Single-run pass rates are noisy — this gives variance visibility.

set -uo pipefail

RUNS=${1:-3}
RESULTS="/tmp/shakedown_variance_results.txt"
> "$RESULTS"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  SHAKEDOWN VARIANCE — ${RUNS} runs of the 10-project core    "
echo "╚══════════════════════════════════════════════════════╝"

for i in $(seq 1 "$RUNS"); do
    echo ""
    echo "################## RUN $i / $RUNS ##################"
    bash /data3/drydock/scripts/shakedown_suite.sh 2>&1
    # After each run, the suite wrote /tmp/shakedown_suite_results.txt — grab it
    if [ -f /tmp/shakedown_suite_results.txt ]; then
        while IFS='|' read -r proj verdict; do
            printf "RUN%d|%s|%s\n" "$i" "$(echo $proj | xargs)" "$(echo $verdict | xargs)" >> "$RESULTS"
        done < /tmp/shakedown_suite_results.txt
    fi
done

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  VARIANCE REPORT                                       "
echo "╚══════════════════════════════════════════════════════╝"

python3 <<PY
import collections
from pathlib import Path

rows = [l.strip().split('|') for l in Path("$RESULTS").read_text().splitlines() if l.strip()]
projs = collections.defaultdict(list)
for run, proj, verdict in rows:
    projs[proj].append(verdict)

print(f"\n{'Project':<30} {' '.join(f'R{i+1:>2}' for i in range(${RUNS}))}  pass/runs")
print('-' * 60)
total_pass = 0
total_runs = 0
for proj, verdicts in sorted(projs.items()):
    marks = ' '.join('✓ ' if v == 'PASS' else '✗ ' for v in verdicts)
    passes = verdicts.count('PASS')
    runs = len(verdicts)
    total_pass += passes
    total_runs += runs
    stability = 'stable' if passes == runs else ('flaky' if passes > 0 else 'broken')
    print(f"{proj:<30} {marks}  {passes}/{runs}  [{stability}]")

print('-' * 60)
print(f"{'TOTAL':<30} {' ':<{${RUNS} * 3 - 1}}  {total_pass}/{total_runs}  ({100 * total_pass / total_runs:.0f}%)")
PY
