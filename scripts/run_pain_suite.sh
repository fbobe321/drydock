#!/bin/bash
# Run the user-pain harness against several real PRDs to confirm
# drydock isn't reproducing the documented failure modes.
#
# For each project we snapshot PRD.md to PRD.master.md before the run
# so the harness can restore it (the harness already does this if a
# master exists).

set -uo pipefail

PYTHON="/home/bobef/miniconda3/bin/python3"
HARNESS="/data3/drydock/scripts/user_pain_test.py"
PROJECTS_DIR="/data3/drydock_test_projects"
RESULTS="/tmp/pain_suite_results.txt"
> "$RESULTS"

# Format: project_dir:package_name:prompt
declare -a SUITE=(
    "01_roman_converter:roman_converter:review the PRD and build the package"
    "06_codec:codec:review the PRD and build the package"
    "08_todo_list:todo_manager:review the PRD and build the package"
    "10_version_control:minivc:review the PRD and build the package"
    "45_prime_tool:prime_tool:review the PRD and build the package"
    "83_color_converter:color_converter:review the PRD and build the package"
    "101_json_pretty_printer:json_pretty_printer:review the PRD and build the package"
    "109_csv_sorter:csv_sorter:review the PRD and build the package"
    "170_makefile_gen:makefile_gen:review the PRD and build the package"
    "301_json_pipeline:json_pipeline:review the PRD and build the package"
)

pass=0
fail=0
total=0

echo "╔════════════════════════════════════════════════════╗"
echo "║  USER-PAIN SUITE — ${#SUITE[@]} projects              "
echo "╚════════════════════════════════════════════════════╝"

for entry in "${SUITE[@]}"; do
    IFS=':' read -r proj pkg prompt <<< "$entry"
    cwd="$PROJECTS_DIR/$proj"
    total=$((total + 1))

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[$total/${#SUITE[@]}] $proj ($pkg)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Snapshot PRD as master so the harness can reset between runs
    if [ -f "$cwd/PRD.md" ]; then
        cp "$cwd/PRD.md" "$cwd/PRD.master.md"
    fi

    PYTHONUNBUFFERED=1 "$PYTHON" -u "$HARNESS" \
        --cwd "$cwd" \
        --prompt "$prompt" \
        --pkg "$pkg"
    rc=$?

    if [ $rc -eq 0 ]; then
        pass=$((pass + 1))
        echo "$proj | PASS" >> "$RESULTS"
    else
        fail=$((fail + 1))
        echo "$proj | FAIL" >> "$RESULTS"
    fi
done

echo ""
echo "╔════════════════════════════════════════════════════╗"
echo "║  SUITE RESULTS: $pass/$total PASS"
echo "╚════════════════════════════════════════════════════╝"
cat "$RESULTS"
