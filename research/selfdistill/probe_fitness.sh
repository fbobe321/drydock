#!/usr/bin/env bash
# probe_fitness.sh <container> — TIER-C proxy gradient, independent of the hidden checker.
#
# WHY: the checker-derived ladder (ctrf_fitness.py: dead 0 / crashed .33 / asserted .66 /
# pass 1) collapses to a flat 0 when the solution never runs — exactly the 0/N flatline
# case where the ratchet has nothing to climb and QD collapses to one niche. These
# milestones are observable BEFORE any test passes, so they give the pawl a gradient on
# tasks the checker is silent about.
#
# ANTI-CHEAT: inspects /app ONLY. It never reads /tests, never runs the checker, and is
# fully deterministic (no model, no judge) — the same RL-style legitimacy as the existing
# reward. Emits a single integer 0..4; higher = closer to something that can pass.
#
#   1  the agent WROTE something (source files present in /app)
#   2  + it is syntactically valid (py_compile / gcc -fsyntax-only / cargo check / node --check)
#   3  + an entry point RUNS without a traceback
#   4  + it produced a non-source OUTPUT artifact
set -u
ctr="${1:?container}"
docker exec -i "$ctr" bash -s <<'PROBE' 2>/dev/null || echo 0
set -u
cd /app 2>/dev/null || { echo 0; exit 0; }
score=0

# 1 — any source the agent could have written
srcs=$(find . -maxdepth 3 -type f \( -name '*.py' -o -name '*.c' -o -name '*.cpp' -o -name '*.rs' \
        -o -name '*.js' -o -name '*.ts' -o -name '*.go' -o -name '*.sh' \) \
        -not -path './node_modules/*' -not -path './.git/*' 2>/dev/null | head -40)
[ -n "$srcs" ] && score=1 || { echo 0; exit 0; }

# 2 — syntactic validity (cheap, no execution)
ok=1
for f in $srcs; do
  case "$f" in
    *.py) python3 -m py_compile "$f" 2>/dev/null || ok=0 ;;
    *.c)   gcc -fsyntax-only "$f" 2>/dev/null || ok=0 ;;
    *.cpp) g++ -fsyntax-only "$f" 2>/dev/null || ok=0 ;;
    *.js)  node --check "$f" 2>/dev/null || ok=0 ;;
    *.sh)  bash -n "$f" 2>/dev/null || ok=0 ;;
  esac
  [ "$ok" = 0 ] && break
done
[ -f Cargo.toml ] && { cargo check --quiet 2>/dev/null || ok=0; }
[ "$ok" = 1 ] && score=2

# 3 — an entry point actually RUNS (no traceback / non-zero exit)
if [ "$score" -ge 2 ]; then
  entry=$(ls main.py app.py run.py solution.py cli.py 2>/dev/null | head -1)
  [ -z "$entry" ] && entry=$(printf '%s\n' $srcs | grep -E '\.py$' | head -1)
  if [ -n "$entry" ] && timeout 45 python3 "$entry" >/tmp/_probe.out 2>&1; then
    grep -qE 'Traceback|SyntaxError' /tmp/_probe.out || score=3
  fi
fi

# 4 — a non-source OUTPUT artifact exists (the shape a checker usually looks for)
if [ "$score" -ge 3 ]; then
  arts=$(find . -maxdepth 3 -type f \( -name '*.json' -o -name '*.csv' -o -name '*.txt' \
          -o -name '*.png' -o -name '*.out' -o -name '*.bin' -o -name '*.db' \) \
          -not -path './.git/*' -newer /app 2>/dev/null | head -3)
  [ -n "$arts" ] && score=4
fi
echo "$score"
PROBE
