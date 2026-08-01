#!/bin/bash
# Drive ONE terminal-bench-2 task through drydock's REAL TUI (no -p, no harness).
#
# This is env-setup + the task's OWN verifier only — the driving and bug-hunting
# are done BY HAND, one task at a time (tmux send-keys / capture-pane). It is NOT
# a batch judge runner (that's banned). Honors: TUI is the whole point.
#
# Usage:
#   source /data3/tbench_local/tui_task_lib.sh
#   ddt_up   fix-git          # build image, run container, install drydock in it
#   ddt_tui  fix-git          # launch the real TUI in tmux session 'ddt_<task>'
#   tmux send-keys -t ddt_fix-git "the instruction text" Enter
#   tmux capture-pane -t ddt_fix-git -p        # watch it work
#   ddt_verify fix-git        # copy tests in, run test.sh, print reward (1=pass)
#   ddt_down fix-git          # tear down tmux + container
#
# LLM endpoint inside the container: host.docker.internal:8000 (gemma4). The
# container gets --add-host so the friendly name resolves.

TASKS="${DDT_TASKS:-/data3/tbench_local/tasks/terminal-bench-2}"
DD_VER="${DD_VER:-3.0.46}"            # published drydock-cli on PyPI
LLM_URL="${LLM_URL:-http://host.docker.internal:8000/v1}"

_ddt_ctr()  { echo "ddt_$1"; }
_ddt_img()  { echo "ddt_task_$1"; }
_ddt_sess() { echo "ddt_$1"; }

ddt_up() {
  local task="$1" img ctr workdir
  [ -d "$TASKS/$task" ] || { echo "[ddt] no such task: $task"; return 1; }
  img=$(_ddt_img "$task"); ctr=$(_ddt_ctr "$task")
  echo "[ddt] building image from $task/environment/Dockerfile ..."
  timeout 900 docker build -q -t "$img" "$TASKS/$task/environment" >/dev/null || return 1
  workdir=$(docker image inspect "$img" --format '{{.Config.WorkingDir}}')
  [ -n "$workdir" ] || workdir=/app
  echo "[ddt] workdir in image: $workdir"
  docker rm -f "$ctr" >/dev/null 2>&1
  docker run -d --name "$ctr" \
    --add-host=host.docker.internal:host-gateway \
    -w "$workdir" "$img" sleep infinity >/dev/null || return 1
  echo "[ddt] container $ctr up. installing drydock-cli==$DD_VER via uv ..."
  docker exec "$ctr" bash -lc '
    set -e
    command -v curl >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq curl) >/dev/null 2>&1 || true
    if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
      curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    timeout 600 uv tool install --refresh --quiet "drydock-cli=='"$DD_VER"'" >/dev/null 2>&1
    drydock --version
  ' || { echo "[ddt] drydock install FAILED in $ctr"; return 1; }
  echo "[ddt] ready. instruction.md:"; sed -n '1,40p' "$TASKS/$task/instruction.md"
}

ddt_tui() {
  local task="$1" ctr sess workdir
  ctr=$(_ddt_ctr "$task"); sess=$(_ddt_sess "$task")
  workdir=$(docker inspect "$ctr" --format '{{.Config.WorkingDir}}' 2>/dev/null)
  [ -n "$workdir" ] || workdir=/app
  tmux kill-session -t "$sess" 2>/dev/null
  tmux new-session -d -s "$sess" -x 210 -y 50 \
    "docker exec -it $ctr bash -lc 'export PATH=\$HOME/.local/bin:\$PATH; cd $workdir; drydock --provider vllm --base-url $LLM_URL --model gemma4'"
  echo "[ddt] TUI launching in tmux session '$sess' (cwd $workdir in $ctr)."
  echo "[ddt] feed it:   tmux send-keys -t $sess \"...\" Enter"
  echo "[ddt] watch it:  tmux capture-pane -t $sess -p"
}

ddt_verify() {
  local task="$1" ctr reward
  ctr=$(_ddt_ctr "$task")
  echo "[ddt] copying tests + running the task's own verifier (test.sh) ..."
  docker exec "$ctr" mkdir -p /tests /logs/verifier
  docker cp "$TASKS/$task/tests/." "$ctr:/tests/" >/dev/null
  timeout 300 docker exec "$ctr" bash /tests/test.sh 2>&1 | tail -25
  reward=$(docker exec "$ctr" cat /logs/verifier/reward.txt 2>/dev/null)
  echo "[ddt] ===== reward: ${reward:-?}  (1=pass, 0=fail) ====="
}

ddt_down() {
  local task="$1"
  tmux kill-session -t "$(_ddt_sess "$task")" 2>/dev/null
  docker rm -f "$(_ddt_ctr "$task")" >/dev/null 2>&1
  echo "[ddt] torn down $task"
}
