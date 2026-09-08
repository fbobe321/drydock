#!/usr/bin/env bash
# loop_ratchet.sh — the WITH-LOOP arm of the loop control (PRE_REGISTRATION.md).
#
# Identical to the plain ratchet in every way EXCEPT it pins the first-principles
# `Ledger` tool (ground-truth ledger + bottleneck register) so the model has a
# persistent, cross-round place to record verified-vs-assumed facts, rank unknowns by
# decision impact, decompose the problem, and attack the highest share*headroom lever.
# No extra prompt text is added — §17.1 measured that the *instruction* form backfires;
# this arm changes only which TOOL is available, which is the single variable under test.
#
# Usage: loop_ratchet.sh <task> [MAX_ROUNDS] [ROUND_BUDGET_S]
# Plain arm for comparison: just run ratchet_solve.sh with the SAME args and no LOOP_PINS.
set -u
export LOOP_PINS='"Ledger"'
exec bash "$(dirname "$0")/../ratchet_solve.sh" "$@"
