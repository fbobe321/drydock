#!/usr/bin/env bash
# eratchet — the EVOLUTIONARY ratchet (Quality-Diversity archive + crossover + variation policy).
# Thin front-end so "eratchet" is a real command; the engine is ratchet_evolve.sh.
# Usage: eratchet.sh <task> [MAX_ROUNDS] [ROUND_BUDGET_S]
exec bash /data3/tbench_local/frontier/selfdistill/ratchet_evolve.sh "$@"
