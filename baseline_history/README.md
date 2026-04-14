# 412-suite Baseline History

TSVs of `functional_tests.sh` runs across all 99 PRDs in
`/data3/drydock_test_projects/NN_*/` at each iteration of improvements
during the 2026-04-13 overnight session.

## Summary

| Run | Pass rate | Clean | Dirty | Trigger |
| --- | --- | --- | --- | --- |
| results1 | 75.4% (240/314) | 46 | 44 | initial baseline (commit a6d3437) |
| results2 | 82.8% (260/314) | 64 | 33 | noun-phrase prose + /tmp fixtures (96fabc1) |
| results3 | 83.1% (261/314) | 65 | 32 | adjective prose vocab |
| results4 | 84.4% (265/314) | 65 | 32 | multi-slash either-or (0f2afd2) |
| results5 | 85.0% (267/314) | 65 | 32 | plain-name file fixtures (d46c33d) |
| results6 | 86.3% (271/314) | 69 | 28 | ordinal/positional prose (170363b) |
| results7 | 86.3% (271/314) | 69 | 28 | after shakedown v3 (test-gen gains only) |
| results8 | 87.9% (276/314) | 70 | 27 | meta_ralph fixed minivc 0/5→5/5 (e44af9b) |
| results9 | 88.9% (279/314) | 71 | 26 | meta_ralph built 41_calculator 0/3→3/3 (f45281c) |

## How to reproduce

```bash
cd /data3/drydock_test_projects
: > /tmp/baseline.tsv
for d in $(ls | grep -E '^[0-9]{2}_'); do
  if [ -f "$d/functional_tests.sh" ]; then
    cd "$d"
    OUT=$(timeout 30 bash functional_tests.sh 2>&1 || true)
    RESULT=$(echo "$OUT" | tail -5 | grep "^RESULT:" | head -1)
    echo -e "$d\t$RESULT" >> /tmp/baseline.tsv
    cd ..
  fi
done
```

Then summarize with the Python snippet at the top of BASELINE_412.md.

## What drove the improvements

- **+7.4pp from prose-detection heuristic:** PRD arrow-text defaults to
  behavioral tests now; only strong literal signals keep expected-keyword
  grep.
- **+2pp from /tmp and plain-name fixtures:** commands that reference
  myfile.txt, searcher.py, /tmp/messy etc. now have something to act on.
- **+2.6pp from meta_ralph (drydock iteration):** minivc and calculator
  rebuilt via worked examples + stage-1 single-build.

The remaining 26 dirty PRDs split roughly:
- ~8 interactive-stdin (hangman, tic_tac_toe, blackjack, number_guess,
  password_vault, etc.) — can't be tested non-interactively without
  injecting stdin fixtures.
- ~12 real drydock bugs of varying complexity (empty output, missing
  submodule, logic errors in subcommand handlers).
- ~6 unbuilt or partially-built PRDs that would need full drydock
  iteration.
