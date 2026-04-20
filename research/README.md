# research/ — drydock autoresearch loop

Karpathy-style overnight research loop for tuning drydock's admiral
knobs against its own stress harness. Modeled on
`karpathy/autoresearch`:

- **Fixed 5-minute experiment budget** (`kernel.py`). Every variant runs
  against the same mini-PRD and the same 25-prompt stimulus sequence, so
  metric values are comparable across configs.
- **Single mutable surface** (`config_best.toml` → `admiral_tuning.json`).
  Source code, tools, prompts, providers — all frozen.
- **Single metric**: `done_per_minute` with a cliff at >50% skip+timeout
  rate. Higher is better. The cliff prevents configs that "go fast by
  refusing work" from winning.
- **Append-only log**: `results.tsv` grows forever; experiments never
  overwrite prior runs.

## Files

| File | Role | Mutable? |
|---|---|---|
| `README.md` | this | human |
| `kernel.py` | 5-min stress runner | no |
| `experimenter.py` | overnight mutate-measure-promote loop | no |
| `mini_prd.md` | tiny PRD the kernel builds from | no |
| `mini_prompts.txt` | 25 fixed stimulus prompts | no |
| `config_base.toml` | baseline + mutation surface + bounds | no (manual reseeds only) |
| `config_best.toml` | current-best promoted variant | yes (experimenter writes) |
| `results.tsv` | append-only experiment log | yes (append) |
| `staged/*.toml` | per-experiment variant configs | yes (experimenter writes) |
| `STOP` | sentinel file — touch to stop the experimenter cleanly | yes (operator) |

## Isolation

Each kernel invocation creates `/tmp/research_<exp_id>/` with its own
`home/` and `cwd/`. The kernel spawns drydock with `HOME=<tmpdir>/home`,
so drydock reads a sandboxed config + writes sessions under the tmp
dir — no interference with the user's real `~/.drydock` or a concurrent
stress run.

## Mutation surface

See `config_base.toml`. The experimenter only touches knobs declared
there with `mutable = true`. Bounds mirror
`drydock/admiral/tuning.py::KNOB_BOUNDS` — if that changes, mirror it
here. Out-of-bounds values fail validation inside the kernel before
drydock ever starts, rather than being silently clipped by admiral.

Currently mutated:
- `per_prompt_budget_sec`, `hard_stop_tool_calls`
- `wrap_up_warn_at`, `stop_now_warn_at`
- `temperature`
- `loop_detector_window`, `struggle_threshold`

Also mutable via `[env_flags]`:
- `DRYDOCK_AUTO_CONTINUE_DISABLE`

## Running

**Sanity check the kernel once** (should produce one row in results.tsv):

```bash
cd /data3/drydock
/home/bobef/miniforge3/envs/drydock/bin/python3 research/kernel.py \
    --config research/config_base.toml \
    --results-tsv research/results.tsv \
    --exp-id sanity_$(date +%s) \
    --note "baseline sanity"
```

If that row looks sane (metric > 0, done > 0), kick off the overnight loop:

```bash
/home/bobef/miniforge3/envs/drydock/bin/python3 research/experimenter.py \
    --results-tsv research/results.tsv \
    --cooldown-s 10 \
    2>&1 | tee /tmp/experimenter_$(date +%s).log
```

Stop it with `touch research/STOP` or `Ctrl-C`. The STOP sentinel
approach is safer overnight — SIGINT from a disconnecting SSH can kill
an in-flight kernel mid-experiment.

## When NOT to run the experimenter

- **While a long-running stress is live on the same GPU.** Both kernels
  and the stress harness share vLLM at `:8000`; running both starves
  everyone. The v10 stress run is doing exactly this as of 2026-04-19,
  which is why the experimenter is scaffolded-but-not-running.
- **Before sanity-checking kernel.py once** with the baseline config.
  If the baseline config can't complete a 5-min run, random variants
  definitely can't.

## Reading results.tsv

```tsv
ts	exp_id	git_commit	config_sha	metric	done	skip	timeout	recycle	elapsed_s	note
```

Plot `ts` vs `metric` to see whether search is finding improvements.
Group by `config_sha` to check variance across repeated runs of the
same config (important signal — a flaky metric means experiments need
replication before promotion).

## Known limitations (intentional)

- **Single-sample metric.** Promoting on one run is noisy. If variance
  matters, add a "replicate winners N times" pass before promotion.
- **Random search only.** Bayesian optimizers or LLM-proposed mutations
  would be strictly better. Earn those by hitting the ceiling of random
  first.
- **No cross-(model, task) sweeps.** All experiments run the same PRD
  + prompts. Extend `target` + `mini_prd.md` matrix if you want to
  tune per-workload.
- **No early-stop inside an experiment.** If a variant is clearly
  useless (100% skips by prompt 5), the kernel still runs the full 5
  minutes. Acceptable for now; the hard ceiling caps total waste.
