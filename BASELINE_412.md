# 412-suite Functional Test Baseline

Date: 2026-04-13
Test generator: `scripts/auto_generate_tests.py` (commit 0f2afd2).

## Overall (latest)

| Metric | Value |
| --- | --- |
| Total PRDs in suite | 99 |
| Total test runs | 314 |
| Pass | 295 |
| Fail | 19 |
| **Pass rate** | **93.9%** |
| Clean PRDs (all tests pass) | 84 |
| PRDs with ≥1 failure | 13 |

## Progression through generator iterations

| Commit | Pass rate | Clean | Dirty | Note |
| --- | --- | --- | --- | --- |
| a6d3437 | 75.4% | 46 | 44 | conservative literal detection |
| 96fabc1 | 82.8% | 64 | 33 | noun-phrase prose + /tmp fixtures |
| 0f2afd2 | 84.4% | 65 | 32 | adjective prose + multi-slash either-or |
| d46c33d | 85.0% | 65 | 32 | plain-name file fixtures (.py/.txt/.csv) |
| 170363b | 86.3% | 69 | 28 | ordinal/positional prose (first/last/max/up/down) |
| meta_ralph | 87.9% | 70 | 27 | meta_ralph fixed minivc 0/5→5/5 in 58s (cli_subcommand_dispatch worked example) |
| meta_ralph | 88.9% | 71 | 26 | meta_ralph built 41_calculator from scratch 0/3→3/3 in 108s |
| ed07c9e | 89.2% | 72 | 25 | skip shell-cmd next-lines + case-insensitive grep |
| 09b8c4f | 89.5% | 73 | 24 | styling/formatting prose markers |
| meta_ralph | 89.8% | 74 | 23 | fraction_calc 3/4→4/4 |
| meta_ralph | 90.1% | 75 | 22 | prime_tool 3/4→4/4 |
| meta_ralph | 90.4% | 76 | 21 | graph_calc 2/3→3/3 |
| batch x5 | 92.0% | 80 | 17 | unit_converter/port_scanner/trivia_gen/hash_generator/duplicate_finder |
| batch x8 | 93.9% | 84 | 13 | lorem_generator/file_renamer/ini_parser/token_generator + partial on 3 |

Remaining 32 dirty PRDs are predominantly REAL drydock build bugs,
not test false positives — the test harness is now a reasonable
ground truth for drydock iteration to target.

## PRDs with failures (targets for drydock iteration)

Grouped by likely failure mode (first pass, not yet investigated):

### Silent exit (empty output — no call to main, stub classes)

- 10_version_control — 0/5 — `AttributeError: 'function' object has no attribute 'run'`
  (model wrote `init.run()` but `init` is a function, not a class)
- 26_file_hasher — 0/3 — empty output
- 27_file_organizer — 0/3 — empty output
- 71_hangman — 0/2 — empty output

### Mostly-working, 1 test fails (likely a single missing subcommand or flag)

- 05_regex_tool, 17_text_wrapper, 18_anagram_finder, 20_sentence_parser,
  21_file_renamer, 22_duplicate_finder, 28_disk_usage, 31_json_formatter,
  35_ini_parser, 36_env_manager, 38_schema_validator, 42_unit_converter,
  44_matrix_calc, 45_prime_tool, 47_fraction_calc, 48_fibonacci_gen,
  55_cipher_tool, 57_checksum_tool, 69_port_scanner, 78_sudoku_solver,
  79_trivia_gen, 99_snippet_mgr — all 1-fail cases

### Partial builds (≥50% tests failing)

- 11_word_counter, 15_lorem_generator, 30_symlink_manager, 33_config_manager,
  49_graph_calc, 50_polynomial_solver, 51_hash_generator, 53_password_vault,
  56_token_generator, 58_secret_sharer, 59_steganography_text, 68_api_mocker,
  72_number_guess, 75_tic_tac_toe, 76_blackjack, 81_cron_parser,
  87_timer_tool, 88_contact_book

## How to reproduce

```bash
cd /data3/drydock_test_projects/<prd>
bash functional_tests.sh
```

Or compute fresh baseline:

```bash
cd /data3/drydock_test_projects
for d in $(ls | grep -E '^[0-9]{2}_'); do
  if [ -f "$d/functional_tests.sh" ]; then
    cd "$d" && bash functional_tests.sh 2>&1 | tail -1 | sed "s|^|[$d] |"
    cd ..
  fi
done
```

## Use

- Run `scripts/ralph_loop.py` or `scripts/meta_ralph_loop.py` on a failing
  PRD to measure whether drydock iteration moves the needle.
- Track deltas vs this baseline to quantify model + harness improvements.
- Focus fixes on the "Silent exit" group first — they're 0/N cases so any
  fix is a measurable improvement.
