#!/usr/bin/env bash
# Drydock quickstart — zero-to-first-task with one command.
#
#   curl -fsSL https://raw.githubusercontent.com/fbobe321/drydock/main/scripts/quickstart.sh | bash
#   # or, from a clone:  bash scripts/quickstart.sh
#
# Two paths, both keep your data on-box except the model call you choose:
#   LOCAL     (default) — download a strong local coder model + serve it with llama.cpp,
#                         fully air-gapped after the one-time download. Needs a GPU box
#                         with `llama-server` (llama.cpp) on PATH.
#   FRONTIER  — no GPU? point Drydock at a keyed OpenAI-compatible endpoint to try it in
#               one minute (your key + prompts leave the box for that provider only).
#
# Choose non-interactively with:  DRYDOCK_QUICKSTART_MODE=local|frontier bash scripts/quickstart.sh
set -euo pipefail

# ── featured local model (see docs/gtm_prd.md W6: a strong coder model as the default
#    first experience so the trial doesn't stall on a weaker model) ──────────────────
MODEL_ALIAS="qwen-coder"
HF_REPO="bartowski/Qwen2.5-Coder-32B-Instruct-GGUF"
GGUF_FILE="Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf"
MODELS_DIR="${DRYDOCK_MODELS_DIR:-$HOME/.drydock/models}"
PORT="${DRYDOCK_PORT:-8000}"
CTX="${DRYDOCK_CTX:-65536}"

say() { printf '\033[36m⚓ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ── 1. install drydock-cli (prefer pipx for an isolated CLI) ──────────────────────────
install_drydock() {
  if have drydock; then say "drydock already installed ($(drydock --version 2>/dev/null || echo present))"; return; fi
  say "installing drydock-cli…"
  if have pipx; then pipx install drydock-cli
  elif have pip;  then pip install --user drydock-cli
  elif have pip3; then pip3 install --user drydock-cli
  else die "need pipx or pip on PATH to install drydock-cli"; fi
  have drydock || warn "drydock not on PATH yet — you may need to restart your shell (pipx ensurepath)."
}

# ── 2a. LOCAL path: fetch a GGUF + serve it with llama.cpp, then launch drydock ───────
run_local() {
  have llama-server || die "llama-server (llama.cpp) not found on PATH. Build/install llama.cpp first, \
then re-run — or use the FRONTIER path (DRYDOCK_QUICKSTART_MODE=frontier) to try Drydock with no GPU."
  mkdir -p "$MODELS_DIR"
  local gguf="$MODELS_DIR/$GGUF_FILE"
  if [ ! -f "$gguf" ]; then
    say "downloading $GGUF_FILE (~20GB, one time) → $MODELS_DIR"
    if have huggingface-cli; then
      huggingface-cli download "$HF_REPO" "$GGUF_FILE" --local-dir "$MODELS_DIR" --local-dir-use-symlinks False
    elif have curl; then
      curl -fL --retry 3 -o "$gguf" "https://huggingface.co/$HF_REPO/resolve/main/$GGUF_FILE?download=true"
    else die "need huggingface-cli or curl to download the model"; fi
  else
    say "model already present: $gguf"
  fi
  say "starting llama-server on :$PORT as '$MODEL_ALIAS' (ctx $CTX)…"
  # background the server; --alias makes the OpenAI model id match what we pass to drydock
  llama-server -m "$gguf" -c "$CTX" --port "$PORT" --alias "$MODEL_ALIAS" --jinja >/tmp/drydock-llama.log 2>&1 &
  local pid=$!
  say "llama-server pid $pid (log: /tmp/drydock-llama.log) — waiting for it to come up…"
  for _ in $(seq 1 60); do
    if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then break; fi
    sleep 2
  done
  curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1 || die "server didn't become healthy — see /tmp/drydock-llama.log"
  say "model ready. launching Drydock…"
  exec drydock --provider vllm --base-url "http://localhost:$PORT/v1" --model "$MODEL_ALIAS"
}

# ── 2b. FRONTIER path: keyed OpenAI-compatible endpoint (no GPU needed for the trial) ─
run_frontier() {
  local base model
  base="${DRYDOCK_BASE_URL:-https://api.openai.com/v1}"
  model="${DRYDOCK_MODEL:-gpt-4o}"
  if [ -z "${OPENAI_API_KEY:-}" ] && [ -z "${DRYDOCK_API_KEY:-}" ]; then
    warn "set OPENAI_API_KEY (or DRYDOCK_API_KEY) for a keyed provider, e.g.:"
    warn "  OPENAI_API_KEY=sk-... DRYDOCK_QUICKSTART_MODE=frontier bash scripts/quickstart.sh"
    die "no API key in environment"
  fi
  say "launching Drydock against $model @ $base (keyed provider)…"
  exec drydock --provider openai --base-url "$base" --model "$model"
}

main() {
  install_drydock
  local mode="${DRYDOCK_QUICKSTART_MODE:-}"
  if [ -z "$mode" ]; then
    if [ -t 0 ]; then
      printf '\nChoose a model backend:\n  [1] LOCAL — download %s + serve with llama.cpp (air-gapped, needs a GPU)\n  [2] FRONTIER — keyed OpenAI-compatible endpoint (try it now, no GPU)\n> ' "$GGUF_FILE"
      read -r choice; case "$choice" in 2) mode=frontier;; *) mode=local;; esac
    else
      mode=local   # piped/non-interactive → default to the air-gapped path
    fi
  fi
  case "$mode" in
    local)    run_local;;
    frontier) run_frontier;;
    *) die "unknown DRYDOCK_QUICKSTART_MODE='$mode' (use local|frontier)";;
  esac
}
main "$@"
