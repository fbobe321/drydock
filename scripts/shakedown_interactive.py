#!/usr/bin/env python3
"""Interactive shakedown — simulates a real user session with back-and-forth.

Unlike the basic shakedown (one prompt → wait), this script sends a
SEQUENCE of prompts that exercise planning, building, testing, editing,
and troubleshooting.  It watches each response before sending the next.

Usage:
    python3 scripts/shakedown_interactive.py \
        --cwd /data3/drydock_test_projects/403_tool_agent \
        --pkg tool_agent
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pexpect

DRYDOCK_BIN = "/home/bobef/miniforge3/envs/drydock/bin/drydock"
SESSION_ROOT = Path.home() / ".drydock" / "logs" / "session"

# ── Conversation scripts per PRD ─────────────────────────────────────
# Each step: (prompt_text, wait_condition, max_wait_seconds)
# wait_condition: "msgs>=N" or "writes>=N" or "done"

SCRIPTS: dict[str, list[tuple[str, str, int]]] = {

    # ═══════════════════════════════════════════════════════════════
    # Tool Agent — 24 steps (HARD: multi-step chaining, plugins, memory)
    # ═══════════════════════════════════════════════════════════════
    "tool_agent": [
        # ── Phase 1: Planning ──
        ("Review the PRD and create a plan. This agent needs 6 built-in "
         "tools, multi-step chaining, pipe syntax, plugin loading, and "
         "conversation memory. List all 8 files and their responsibilities.",
         "msgs>=3", 120),
        ("Explain how the pipe syntax will work. How will "
         "'file_reader README.md | word_counter' be parsed and executed? "
         "What about the json_tool path extraction?",
         "msgs>={prev}+2", 90),
        ("Create a todo list with ALL steps. Then start building — "
         "write tools.py with all 6 built-in tools first, then parser.py "
         "for pipe syntax. Do NOT stop between items.",
         "writes>=3", 150),
        # ── Phase 2: Building ──
        ("Continue the todo list. Build agent.py with multi-step "
         "planning, memory.py, plugins.py, cli.py, __main__.py.",
         "writes>=7", 180),
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        ("Run python3 -m {pkg} --list-tools and verify all 6 built-in "
         "tools appear.",
         "msgs>={prev}+2", 90),
        # ── Phase 3: Testing each tool ──
        ("Test the calculator: python3 -m {pkg} \"What is 23 * 47?\"",
         "msgs>={prev}+2", 90),
        ("Test the file reader: python3 -m {pkg} \"Read PRD.md\"",
         "msgs>={prev}+2", 90),
        ("Test the word counter: python3 -m {pkg} \"Count words in PRD.md\"",
         "msgs>={prev}+2", 90),
        ("Test the grep tool: python3 -m {pkg} \"Search for class in *.py\"",
         "msgs>={prev}+2", 90),
        # ── Phase 4: Pipe syntax ──
        ("Test pipe syntax: python3 -m {pkg} \"file_reader PRD.md | word_counter\"",
         "msgs>={prev}+2", 120),
        ("If pipes didn't work, fix it with search_replace on parser.py. "
         "Then test again.",
         "msgs>={prev}+3", 120),
        # ── Phase 5: Feature addition (exercises search_replace) ──
        ("Add a new tool called 'date_tool' that returns the current "
         "date and time. Use search_replace on tools.py — do NOT "
         "rewrite the whole file.",
         "msgs>={prev}+4", 120),
        ("Test it: python3 -m {pkg} \"What is today's date?\"",
         "msgs>={prev}+2", 90),
        # ── Phase 6: Plugin system ──
        ("Create a plugins/ directory with a sample plugin: "
         "plugins/reverse_tool.py that reverses a string. The agent "
         "should auto-discover it.",
         "msgs>={prev}+3", 120),
        ("Test: python3 -m {pkg} --list-tools — does the plugin appear? "
         "Test: python3 -m {pkg} \"Reverse the word hello\"",
         "msgs>={prev}+3", 120),
        # ── Phase 7: Bug hunt ──
        ("What happens if I pass an empty query? Try: python3 -m {pkg} \"\"",
         "msgs>={prev}+2", 90),
        ("Fix any crash from the empty query. Use search_replace.",
         "msgs>={prev}+3", 120),
        # ── Phase 8: Verbose mode ──
        ("Add a --verbose flag that shows the full reasoning trace "
         "including [THINK], [PLAN], [TOOL], [ANSWER] steps. "
         "Use search_replace on cli.py.",
         "msgs>={prev}+4", 120),
        ("Test verbose mode: python3 -m {pkg} --verbose \"What is 5+3?\"",
         "msgs>={prev}+2", 90),
        # ── Phase 9: Ideas and wrap-up ──
        ("What features would you add next? Give me 5 ideas for "
         "improving this agent.",
         "msgs>={prev}+2", 90),
        ("Run all the tests one more time — calculator, file reader, "
         "word counter, grep, date, pipe, plugin. Report which pass/fail.",
         "msgs>={prev}+3", 150),
        ("Update the todo list — mark everything done. Show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md with usage examples including pipe syntax.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Stock Screener — 24 steps (HARD: sectors, watchlists, compare)
    # ═══════════════════════════════════════════════════════════════
    "stock_screener": [
        # ── Planning ──
        ("Review the PRD. This screener needs sector grouping, watchlist "
         "management, snapshot comparison, and percentile-based ranking. "
         "Plan the 9 files and data flow.",
         "msgs>=3", 120),
        ("How will percentile normalization work for the ranking? "
         "What about ties? How will the sector breakdown compute "
         "aggregate statistics?",
         "msgs>={prev}+2", 90),
        ("Create a comprehensive todo list and start building. Write "
         "data.py and screener.py first. Do NOT stop between items.",
         "writes>=3", 150),
        # ── Building ──
        ("Continue. Build sectors.py, watchlist.py, compare.py, "
         "formatter.py, cli.py, __main__.py.",
         "writes>=8", 180),
        ("Create portfolio.csv with 15 stocks across 4 sectors "
         "(Technology, Financials, Healthcare, Energy). Include edge "
         "cases: missing values, zero book_value, negative debt.",
         "msgs>={prev}+2", 120),
        # ── Testing basic commands ──
        ("Test: python3 -m {pkg} screen --data portfolio.csv --pb-max 1.5",
         "msgs>={prev}+2", 90),
        ("Test filtering by sector: python3 -m {pkg} screen --data "
         "portfolio.csv --sector Technology",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} rank --data portfolio.csv --top 5",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} rank --data portfolio.csv --by value",
         "msgs>={prev}+2", 90),
        # ── Sector analysis ──
        ("Test: python3 -m {pkg} sectors --data portfolio.csv",
         "msgs>={prev}+2", 90),
        ("Read sectors.py and explain the aggregate calculation. "
         "Does it handle sectors with only 1 stock correctly?",
         "msgs>={prev}+2", 120),
        # ── Watchlist management ──
        ("Save a watchlist: python3 -m {pkg} watchlist save \"value_picks\" "
         "--pb-max 1.5 --debt-max 0.5 --insider-min 5",
         "msgs>={prev}+2", 120),
        ("Load it: python3 -m {pkg} watchlist load \"value_picks\" --data portfolio.csv",
         "msgs>={prev}+2", 90),
        ("List all watchlists: python3 -m {pkg} watchlist list",
         "msgs>={prev}+2", 90),
        # ── Snapshot comparison ──
        ("Create a second CSV called q2.csv — copy portfolio.csv but "
         "change some prices and add 1 new stock, remove 1 old stock.",
         "msgs>={prev}+2", 120),
        ("Test: python3 -m {pkg} compare --old portfolio.csv --new q2.csv",
         "msgs>={prev}+2", 120),
        # ── Export ──
        ("Test: python3 -m {pkg} export --data portfolio.csv --format json --output results.json",
         "msgs>={prev}+2", 90),
        # ── Bug hunt ──
        ("What happens with an empty CSV? Create empty.csv with just "
         "headers and test: python3 -m {pkg} screen --data empty.csv",
         "msgs>={prev}+3", 120),
        ("Fix any crash. Use search_replace.",
         "msgs>={prev}+3", 120),
        # ── Summary ──
        ("Test: python3 -m {pkg} summary --data portfolio.csv",
         "msgs>={prev}+2", 90),
        # ── Ideas ──
        ("What would a real quantitative analyst want from this tool? "
         "Give me 5 feature ideas.",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Run through all commands one final time: screen, rank, "
         "sectors, watchlist, compare, export, summary. Report results.",
         "msgs>={prev}+3", 180),
        ("Update the todo list — everything should be done.",
         "msgs>={prev}+2", 90),
        ("Write a README.md covering all subcommands.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Eval Harness — 24 steps (HARD: pipelines, bootstrap stats, diff)
    # ═══════════════════════════════════════════════════════════════
    "eval_harness": [
        # ── Planning ──
        ("Review the PRD. This harness needs evaluator pipelines, "
         "bootstrap significance testing, per-category breakdown, and "
         "diff reports. Plan the 8 files.",
         "msgs>=3", 120),
        ("Explain how evaluator pipelines work. How does "
         "'normalize,exact' chain two evaluators? How will the bootstrap "
         "significance test work with 1000 resamples?",
         "msgs>={prev}+2", 90),
        ("Create a todo list. Then build evaluators.py with exact, "
         "fuzzy, contains, regex, code, normalize, and numeric. "
         "Execute without stopping.",
         "writes>=2", 150),
        # ── Building ──
        ("Continue. Build runner.py with timeout support, stats.py "
         "with bootstrap, diff.py, report.py, cli.py, __main__.py.",
         "writes>=7", 180),
        ("Create tasks.json with 20 test cases: 5 math, 5 logic, "
         "5 coding, 5 extraction. Each must have an 'id', 'question', "
         "'expected', 'type', and 'category' field.",
         "msgs>={prev}+2", 120),
        # ── Testing evaluators ──
        ("Run: python3 -m {pkg} run --dataset tasks.json --evaluator exact",
         "msgs>={prev}+3", 120),
        ("Run: python3 -m {pkg} run --dataset tasks.json --evaluator fuzzy --threshold 0.8",
         "msgs>={prev}+3", 120),
        ("Run: python3 -m {pkg} run --dataset tasks.json --evaluator contains",
         "msgs>={prev}+3", 120),
        # ── Evaluator pipeline ──
        ("Test the pipeline: python3 -m {pkg} run --dataset tasks.json "
         "--evaluator \"normalize,exact\"",
         "msgs>={prev}+3", 120),
        ("Compare results: did the normalize pipeline pass more tasks "
         "than plain exact? Show both reports.",
         "msgs>={prev}+2", 90),
        # ── Per-category breakdown ──
        ("Run: python3 -m {pkg} categories --dataset tasks.json",
         "msgs>={prev}+2", 90),
        ("Run: python3 -m {pkg} report --results results.json  — "
         "does the report show per-category accuracy?",
         "msgs>={prev}+2", 120),
        # ── Statistical significance ──
        ("Run: python3 -m {pkg} stats --results results.json — "
         "does it show accuracy with 95% confidence interval?",
         "msgs>={prev}+2", 120),
        # ── Diff report ──
        ("Save the current results. Then run with 'normalize,exact' "
         "to get a second result file. Generate a diff report: "
         "python3 -m {pkg} diff --baseline results.json --candidate results_v2.json",
         "msgs>={prev}+4", 150),
        # ── Code review ──
        ("Read evaluators.py. Walk me through the pipeline chaining "
         "logic. How does normalize feed into exact?",
         "msgs>={prev}+2", 120),
        ("Read stats.py. Is the bootstrap implementation correct? "
         "Does it resample 1000 times?",
         "msgs>={prev}+2", 90),
        # ── Bug hunt ──
        ("What happens with a malformed tasks.json? Create bad.json "
         "with invalid JSON and test: python3 -m {pkg} run --dataset bad.json",
         "msgs>={prev}+3", 120),
        ("Fix any crash. The tool should report the error gracefully.",
         "msgs>={prev}+3", 120),
        # ── Edge cases ──
        ("Test with an empty dataset: create empty.json with [] and "
         "run the evaluator. Should show 0 tasks, no errors.",
         "msgs>={prev}+3", 120),
        # ── Ideas ──
        ("What metrics would make this harness more useful for ML "
         "engineers? Think about confusion matrices, ROC curves, etc.",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Run the full evaluation suite: exact, fuzzy, normalize+exact. "
         "Show the comparison report with per-category breakdown.",
         "msgs>={prev}+3", 180),
        ("Read the final report output. Does it look professional?",
         "msgs>={prev}+2", 90),
        ("Update the todo list — show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md for the package.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Doc QA — 24 steps (HARD: BM25, incremental update, stats)
    # ═══════════════════════════════════════════════════════════════
    "doc_qa": [
        # ── Planning ──
        ("Review the PRD. This system needs BOTH TF-IDF and BM25 "
         "retrieval, incremental updates via file hashing, and corpus "
         "statistics. Plan the 8 files. Remember: package name is doc_qa.",
         "msgs>=3", 120),
        ("Explain the BM25 scoring formula. How does it differ from "
         "TF-IDF? What are k1 and b parameters? How will you implement "
         "both algorithms with just stdlib?",
         "msgs>={prev}+2", 90),
        ("Create a todo list covering all 8 files. Then start building "
         "ingest.py with chunking and file hashing, and tfidf.py. "
         "Execute without stopping.",
         "writes>=3", 150),
        # ── Building ──
        ("Continue the todo list. Build bm25.py, query.py, storage.py, "
         "cli.py, __main__.py.",
         "writes>=7", 180),
        # ── Test data ──
        ("Create test_docs/ with 3 files: one about Python programming, "
         "one about cooking recipes, one about space exploration. "
         "Make each at least 200 words.",
         "msgs>={prev}+2", 120),
        # ── Testing ──
        ("Run: python3 -m {pkg} ingest test_docs/",
         "msgs>={prev}+2", 90),
        ("Run: python3 -m {pkg} list",
         "msgs>={prev}+2", 90),
        ("Query with TF-IDF: python3 -m {pkg} query \"How do you make "
         "pasta?\" --algorithm tfidf",
         "msgs>={prev}+2", 90),
        ("Query with BM25: python3 -m {pkg} query \"How do you make "
         "pasta?\" --algorithm bm25",
         "msgs>={prev}+2", 90),
        ("Compare: query \"What is Python?\" with both algorithms. "
         "Do they rank chunks differently?",
         "msgs>={prev}+3", 120),
        # ── Code review ──
        ("Read tfidf.py. Walk me through the cosine similarity "
         "calculation. Is it correct?",
         "msgs>={prev}+2", 120),
        ("Read bm25.py. Verify the BM25 formula matches the PRD "
         "spec: k1=1.5, b=0.75.",
         "msgs>={prev}+2", 90),
        # ── Incremental update ──
        ("Create a 4th test doc: test_docs/history.txt about ancient "
         "Rome. Then run: python3 -m {pkg} update test_docs/ — it "
         "should only index the NEW file, not re-index everything.",
         "msgs>={prev}+3", 120),
        ("Verify: python3 -m {pkg} list — should show 4 documents. "
         "Query: python3 -m {pkg} query \"Tell me about Rome\"",
         "msgs>={prev}+2", 90),
        # ── Stats ──
        ("Run: python3 -m {pkg} stats — should show total docs, "
         "total chunks, avg chunk size, vocabulary size.",
         "msgs>={prev}+2", 90),
        # ── Feature: chunk size ──
        ("Re-ingest with smaller chunks: python3 -m {pkg} ingest "
         "test_docs/ --chunk-size 100 --overlap 25",
         "msgs>={prev}+2", 90),
        ("Query again and compare results with smaller chunks.",
         "msgs>={prev}+2", 90),
        # ── Bug hunt ──
        ("What happens if I query before ingesting? Delete the .doc_qa "
         "directory and try: python3 -m {pkg} query \"test\"",
         "msgs>={prev}+3", 120),
        ("Fix any crash — it should say 'No index found. Run ingest first.'",
         "msgs>={prev}+3", 120),
        # ── Delete command ──
        ("Delete a document: python3 -m {pkg} delete history.txt — "
         "then verify with python3 -m {pkg} list",
         "msgs>={prev}+3", 120),
        # ── Ideas ──
        ("What would make this system actually useful for real "
         "document search? Give me 5 practical improvements.",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Re-ingest and run 3 different queries with BM25. Show full "
         "output with sources and confidence scores.",
         "msgs>={prev}+3", 150),
        ("Update the todo list — show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md with usage examples for both algorithms.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Prompt Optimizer — 24 steps (HARD: tournament, F1, crossover, CI)
    # ═══════════════════════════════════════════════════════════════
    "prompt_optimizer": [
        # ── Planning ──
        ("Review the PRD. This optimizer uses tournament selection, "
         "crossover, F1 scoring, and bootstrap confidence intervals. "
         "Plan the 8 files and explain the tournament algorithm.",
         "msgs>=3", 120),
        ("How will crossover work? Given two parent prompts, how do you "
         "combine them? What about the bootstrap CI calculation — "
         "resample 100 times and take percentiles?",
         "msgs>={prev}+2", 90),
        ("Create a detailed todo list. Then start building templates.py "
         "with 5 mutation strategies including crossover, and scorer.py "
         "with F1 and bootstrap CI. Execute without stopping.",
         "writes>=3", 150),
        # ── Building ──
        ("Continue the todo list. Build executor.py with negation "
         "handling, optimizer.py with tournament selection, history.py, "
         "cli.py, __main__.py.",
         "writes>=7", 180),
        ("Create data.json with 20 sentiment examples — 7 positive, "
         "7 negative, 6 neutral. Include tricky cases: sarcasm, "
         "negation ('not bad'), mixed ('good but expensive').",
         "msgs>={prev}+2", 90),
        # ── Testing ──
        ("Run: python3 -m {pkg} run --task sentiment --dataset data.json "
         "--iterations 5 --population 6",
         "msgs>={prev}+3", 150),
        ("Show the history: python3 -m {pkg} history --task sentiment",
         "msgs>={prev}+2", 90),
        ("Show the best: python3 -m {pkg} best --task sentiment — "
         "does it show the confidence interval?",
         "msgs>={prev}+2", 90),
        # ── Code review ──
        ("Read executor.py. How does the negation handling work? "
         "Walk me through 'not bad at all' scoring.",
         "msgs>={prev}+2", 120),
        ("Read optimizer.py. Explain the tournament selection: pick 3 "
         "random, keep the best. Is the population evolving correctly?",
         "msgs>={prev}+2", 90),
        ("Read scorer.py. Verify the F1 calculation and bootstrap CI "
         "logic are correct.",
         "msgs>={prev}+2", 90),
        # ── Train/test split ──
        ("Run with a 70/30 split: python3 -m {pkg} run --task sentiment "
         "--dataset data.json --iterations 5 --split 0.7",
         "msgs>={prev}+3", 120),
        ("Does the output show BOTH train and test scores separately?",
         "msgs>={prev}+2", 90),
        # ── Leaderboard ──
        ("Show the leaderboard: python3 -m {pkg} leaderboard --task sentiment",
         "msgs>={prev}+2", 90),
        # ── Feature: topic classification ──
        ("Create topics.json with 15 examples labeled tech/sports/"
         "politics. Then run: python3 -m {pkg} run --task topic "
         "--dataset topics.json --iterations 3",
         "msgs>={prev}+4", 150),
        ("Show the best prompt for topic classification: "
         "python3 -m {pkg} best --task topic",
         "msgs>={prev}+2", 90),
        # ── Compare runs ──
        ("Run: python3 -m {pkg} compare --task sentiment --run1 1 --run2 5 "
         "— does it show which run was better and by how much?",
         "msgs>={prev}+2", 120),
        ("Read history.py. Does each prompt entry track its parent_id "
         "for lineage? Show me the data structure.",
         "msgs>={prev}+2", 90),
        # ── Bug hunt ──
        ("What happens if the dataset has no examples for one class? "
         "Create imbalanced.json with 10 positive and 0 negative. Test.",
         "msgs>={prev}+3", 120),
        ("Fix any division-by-zero or crash. Use search_replace.",
         "msgs>={prev}+3", 120),
        # ── Ideas ──
        ("If we had a real LLM instead of keyword matching, how would "
         "the optimization loop change? Think about prompt engineering "
         "at scale.",
         "msgs>={prev}+2", 90),
        # ── Wrap-up ──
        ("Run the full optimization on both sentiment and topic tasks "
         "with population=6 and iterations=5. Show final scores with "
         "per-class F1 and confidence intervals.",
         "msgs>={prev}+3", 180),
        ("Update the todo list. Show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md explaining the optimization approach "
         "including tournament selection and crossover.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Mini DB — 30 steps (15-MIN TIER: SQL parser, B-tree, txns, joins)
    # ═══════════════════════════════════════════════════════════════
    "mini_db": [
        # ── Phase 1: Planning (3 steps) ──
        ("Review the PRD. This is a mini relational database with SQL "
         "parsing, B-tree indexes, transactions, and JOINs. It has 13 "
         "files. Plan the architecture and file dependencies.",
         "msgs>=3", 120),
        ("Explain how you'll implement the SQL parser without eval(). "
         "Walk me through parsing: SELECT * FROM users WHERE age > 25 "
         "ORDER BY name LIMIT 10",
         "msgs>={prev}+2", 90),
        ("Create a comprehensive todo list. Then start building — write "
         "cli.py, __main__.py, and types.py first. Do NOT stop.",
         "writes>=3", 180),
        # ── Phase 2: Core modules (5 steps) ──
        ("Continue. Build parser.py (SQL tokenizer + recursive descent) "
         "and where.py (comparison operators, AND/OR, LIKE).",
         "writes>=5", 180),
        ("Continue. Build table.py (CRUD, schema validation) and "
         "storage.py (JSON persistence).",
         "writes>=7", 180),
        ("Continue. Build engine.py (query execution), formatter.py "
         "(ASCII table output, CSV). Don't stop.",
         "writes>=9", 180),
        ("Continue. Build index.py (B-tree), transaction.py (WAL), "
         "join.py. Write ALL remaining files.",
         "writes>=13", 240),
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        # ── Phase 3: Testing DDL (4 steps) ──
        ("Test: python3 -m {pkg} exec \"CREATE TABLE users (id INT, name TEXT, age INT)\"",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} tables",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} schema users",
         "msgs>={prev}+2", 90),
        ("Test: python3 -m {pkg} exec \"CREATE TABLE orders (id INT, user_id INT, item TEXT, price FLOAT)\"",
         "msgs>={prev}+2", 90),
        # ── Phase 4: Testing DML (5 steps) ──
        ("Insert test data: python3 -m {pkg} exec \"INSERT INTO users VALUES (1, 'Alice', 30)\" "
         "and add 4 more users with different ages.",
         "msgs>={prev}+3", 120),
        ("Test SELECT: python3 -m {pkg} exec \"SELECT * FROM users\"",
         "msgs>={prev}+2", 90),
        ("Test WHERE: python3 -m {pkg} exec \"SELECT name, age FROM users WHERE age > 25 ORDER BY age DESC\"",
         "msgs>={prev}+2", 90),
        ("Test LIKE: python3 -m {pkg} exec \"SELECT * FROM users WHERE name LIKE '%ali%'\"",
         "msgs>={prev}+2", 90),
        ("Test UPDATE and DELETE: python3 -m {pkg} exec \"UPDATE users SET age=31 WHERE id=1\" "
         "then verify with SELECT.",
         "msgs>={prev}+3", 120),
        # ── Phase 5: Advanced features (4 steps) ──
        ("Insert some orders and test JOIN: python3 -m {pkg} exec "
         "\"SELECT u.name, o.item FROM users u JOIN orders o ON u.id = o.user_id\"",
         "msgs>={prev}+3", 150),
        ("Test indexing: python3 -m {pkg} exec \"CREATE INDEX idx_age ON users(age)\" "
         "then run a SELECT WHERE age = 30. Does it use the index?",
         "msgs>={prev}+3", 120),
        ("Test transactions: python3 -m {pkg} exec \"BEGIN\" then INSERT "
         "a row, then ROLLBACK. Verify the row is gone.",
         "msgs>={prev}+4", 150),
        ("Test CSV export: python3 -m {pkg} export --table users --file users.csv "
         "then read the CSV and verify the data.",
         "msgs>={prev}+3", 120),
        # ── Phase 6: Code review (2 steps) ──
        ("Read parser.py. Walk me through the tokenizer and the "
         "recursive descent logic. Is it handling quoted strings correctly?",
         "msgs>={prev}+2", 120),
        ("Read index.py. Is the B-tree implementation correct? "
         "What's the order? Does it handle duplicate keys?",
         "msgs>={prev}+2", 120),
        # ── Phase 7: Bug hunt (2 steps) ──
        ("What happens with a malformed query? Test: "
         "python3 -m {pkg} exec \"SELCT * FORM users\"",
         "msgs>={prev}+2", 90),
        ("What happens with an empty table? Test SELECT, UPDATE, "
         "DELETE on a table with 0 rows.",
         "msgs>={prev}+3", 120),
        # ── Phase 8: Feature addition (2 steps) ──
        ("Add OFFSET support to SELECT using search_replace on parser.py "
         "and engine.py. Test: SELECT * FROM users LIMIT 2 OFFSET 1",
         "msgs>={prev}+4", 150),
        ("Add COUNT(*) support. Test: python3 -m {pkg} exec "
         "\"SELECT COUNT(*) FROM users WHERE age > 25\"",
         "msgs>={prev}+4", 150),
        # ── Phase 9: Wrap-up (3 steps) ──
        ("Run a comprehensive test: create 2 tables, insert data, "
         "JOIN them, filter with WHERE, ORDER BY, LIMIT. Show full output.",
         "msgs>={prev}+3", 180),
        ("Update the todo list — show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md with usage examples for all features.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Site Generator — 30 steps (15-MIN TIER: MD parser, templates, plugins)
    # ═══════════════════════════════════════════════════════════════
    "site_gen": [
        # ── Phase 1: Planning (3 steps) ──
        ("Review the PRD. This is a static site generator with a "
         "Markdown parser, template inheritance, plugins, sitemap, and "
         "RSS feed. 15 files. Plan the build pipeline.",
         "msgs>=3", 120),
        ("Explain how the Markdown parser will handle nested constructs "
         "like **bold inside [links](url)**. And how will template "
         "inheritance work with {% extends %} and {% block %}?",
         "msgs>={prev}+2", 90),
        ("Create a comprehensive todo list. Then start building — write "
         "cli.py, __main__.py, and config.py first. Do NOT stop.",
         "writes>=3", 180),
        # ── Phase 2: Core modules (6 steps) ──
        ("Continue. Build markdown.py (full Markdown-to-HTML parser) "
         "and frontmatter.py (key:value metadata parsing).",
         "writes>=5", 180),
        ("Continue. Build templates.py (variable substitution + "
         "inheritance) and plugins.py (TOC, reading time, word count).",
         "writes>=7", 180),
        ("Continue. Build builder.py (main pipeline), navigation.py "
         "(auto-nav from file tree).",
         "writes>=9", 180),
        ("Continue. Build sitemap.py, feed.py, server.py, watcher.py, "
         "scaffold.py. Write ALL remaining files.",
         "writes>=14", 240),
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        ("Test init: python3 -m {pkg} init test_site — verify the "
         "directory structure was created with starter files.",
         "msgs>={prev}+3", 120),
        # ── Phase 3: Content creation (3 steps) ──
        ("Create test content: write 3 Markdown files in test_site/content/ "
         "with front matter (title, date, author). Include headings, "
         "bold, italic, links, code blocks, and a list.",
         "msgs>={prev}+2", 120),
        ("Create a test_site/content/posts/ directory with 2 blog posts "
         "that have date and tags in front matter.",
         "msgs>={prev}+2", 120),
        ("Create test_site/templates/base.html with {title}, {nav}, "
         "{content} placeholders and basic HTML structure.",
         "msgs>={prev}+2", 90),
        # ── Phase 4: Build and test (5 steps) ──
        ("Build the site: python3 -m {pkg} build --source test_site/content "
         "--output test_site/dist",
         "msgs>={prev}+3", 150),
        ("Read test_site/dist/index.html — does it have valid HTML "
         "with the Markdown rendered correctly?",
         "msgs>={prev}+2", 90),
        ("Check: does test_site/dist/sitemap.xml exist with all pages?",
         "msgs>={prev}+2", 90),
        ("Check: does test_site/dist/feed.xml exist with blog posts?",
         "msgs>={prev}+2", 90),
        ("Verify the navigation was generated: are all pages linked?",
         "msgs>={prev}+2", 90),
        # ── Phase 5: Code review (3 steps) ──
        ("Read markdown.py. Walk me through parsing a paragraph with "
         "**bold** and [link](url). Is the HTML valid?",
         "msgs>={prev}+2", 120),
        ("Read templates.py. How does inheritance work? Show me how "
         "{% extends \"base.html\" %} resolves.",
         "msgs>={prev}+2", 120),
        ("Read builder.py. What's the build pipeline order? Is it "
         "idempotent (running twice gives same output)?",
         "msgs>={prev}+2", 90),
        # ── Phase 6: Plugin testing (2 steps) ──
        ("Verify plugins work: does the TOC plugin generate a table "
         "of contents from headings? Check the output HTML.",
         "msgs>={prev}+2", 90),
        ("Check reading_time: does a 400-word post show ~2 min?",
         "msgs>={prev}+2", 90),
        # ── Phase 7: Bug hunt (2 steps) ──
        ("What happens with an empty content directory? Test: "
         "python3 -m {pkg} build --source /tmp/empty_site --output /tmp/out",
         "msgs>={prev}+3", 120),
        ("What about a Markdown file with NO front matter? Does it "
         "still render or crash?",
         "msgs>={prev}+3", 120),
        # ── Phase 8: Feature test (2 steps) ──
        ("Test the new post command: python3 -m {pkg} new post "
         "\"Testing the Generator\" — verify the file was created "
         "with correct front matter.",
         "msgs>={prev}+3", 120),
        ("Rebuild the site and verify the new post appears in the "
         "navigation and feed.",
         "msgs>={prev}+3", 150),
        # ── Phase 9: Wrap-up (4 steps) ──
        ("Run a final full build. Show me the output file tree. "
         "Count total files generated.",
         "msgs>={prev}+3", 150),
        ("What improvements would make this production-ready? "
         "Give me 5 ideas.",
         "msgs>={prev}+2", 90),
        ("Update the todo list — show final status.",
         "msgs>={prev}+2", 90),
        ("Write a README.md with usage examples for all commands.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Lang Interpreter — 30 steps (30-MIN TIER: lexer, parser, AST, types)
    # ═══════════════════════════════════════════════════════════════
    "lang_interp": [
        # ── Planning (2) ──
        ("Review the PRD. This is a full programming language interpreter "
         "called 'Bolt' with 16 files: lexer, parser, AST, type checker, "
         "interpreter, REPL, stdlib. Plan the file dependency order.",
         "msgs>=3", 120),
        ("Create a comprehensive todo list. Then start building — write "
         "cli.py, __main__.py, tokens.py, errors.py first. Do NOT stop.",
         "writes>=4", 180),
        # ── Core build (6) ──
        ("Continue. Build lexer.py (tokenizer for keywords, operators, "
         "literals, identifiers, string escapes).",
         "writes>=5", 180),
        ("Continue. Build ast_nodes.py (all expression and statement nodes) "
         "and parser.py (recursive descent → AST).",
         "writes>=7", 240),
        ("Continue. Build types.py (BoltInt, BoltStr, BoltList, etc) and "
         "environment.py (nested scopes, closures).",
         "writes>=9", 180),
        ("Continue. Build interpreter.py (tree-walking eval of the AST).",
         "writes>=10", 180),
        ("Continue. Build stdlib.py, builtins.py, checker.py.",
         "writes>=13", 180),
        ("Continue. Build repl.py, module_loader.py. Write ALL remaining files.",
         "writes>=16", 240),
        # ── Basic testing (5) ──
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        ("Create a test file: test.bolt with 'let x = 42' and "
         "'print(x * 2)'. Run: python3 -m {pkg} run test.bolt",
         "msgs>={prev}+3", 120),
        ("Test variables and arithmetic: create a .bolt file with let, "
         "print, +, -, *, /. Run it.",
         "msgs>={prev}+3", 120),
        ("Test functions: create a .bolt file with fn add(a, b) that "
         "returns a+b, call it, print result. Run it.",
         "msgs>={prev}+3", 120),
        ("Test control flow: create a .bolt file with if/elif/else and "
         "a while loop counting 1-5. Run it.",
         "msgs>={prev}+3", 120),
        # ── Advanced testing (5) ──
        ("Test strings: create a .bolt file with string concatenation, "
         "len(), indexing. Run it.",
         "msgs>={prev}+3", 120),
        ("Test lists: create a .bolt file with list creation, append, "
         "indexing, for loop over list. Run it.",
         "msgs>={prev}+3", 120),
        ("Test the REPL: python3 -m {pkg} repl — try 'let x = 10' then "
         "'print(x + 5)'. Does it work interactively?",
         "msgs>={prev}+3", 120),
        ("Test error reporting: create a .bolt file with a type error. "
         "Does it show line number and context?",
         "msgs>={prev}+2", 90),
        ("Test the token command: python3 -m {pkg} tokens test.bolt — "
         "does it show the token stream?",
         "msgs>={prev}+2", 90),
        # ── Code review (3) ──
        ("Read lexer.py. How does it handle string escapes (\\n, \\t)? "
         "How does it distinguish keywords from identifiers?",
         "msgs>={prev}+2", 120),
        ("Read parser.py. Walk me through parsing: let x = add(1, 2). "
         "What AST nodes are produced?",
         "msgs>={prev}+2", 120),
        ("Read interpreter.py. How does function calling work? "
         "How are closures implemented?",
         "msgs>={prev}+2", 120),
        # ── Bug hunt (3) ──
        ("What happens with division by zero? Test it.",
         "msgs>={prev}+2", 90),
        ("What happens with an undefined variable? Test it.",
         "msgs>={prev}+2", 90),
        ("What about a syntax error — missing closing brace? "
         "Does it report the error clearly?",
         "msgs>={prev}+2", 90),
        # ── Feature addition (2) ──
        ("Add a 'for i in range(n)' example to a test file and run it. "
         "If range doesn't work, fix it with search_replace on stdlib.py.",
         "msgs>={prev}+4", 150),
        ("Write a comprehensive test.bolt that exercises variables, "
         "functions, if/else, loops, lists, strings. Run it and show output.",
         "msgs>={prev}+3", 150),
        # ── Wrap-up (2) ──
        ("Show me the final directory listing of lang_interp/. "
         "How many files total?",
         "msgs>={prev}+2", 90),
        ("Write a README.md with language syntax examples.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Package Manager — 30 steps (30-MIN TIER: resolver, versions, lockfile)
    # ═══════════════════════════════════════════════════════════════
    "pkg_manager": [
        # ── Planning (2) ──
        ("Review the PRD. This is a package manager called 'crate' with "
         "17 files: dependency resolver, version constraints, lock files, "
         "local registry, virtual environments. Plan the architecture.",
         "msgs>=3", 120),
        ("Create a comprehensive todo list. Then start building — write "
         "cli.py, __main__.py, errors.py, config.py first. Do NOT stop.",
         "writes>=4", 180),
        # ── Core build (6) ──
        ("Continue. Build version.py (semver parsing, comparison, "
         "constraint matching: ^, ~, >=, <, wildcard).",
         "writes>=5", 180),
        ("Continue. Build manifest.py (TOML-like parser for crate.toml) "
         "and lockfile.py (lock file generation and reading).",
         "writes>=7", 180),
        ("Continue. Build resolver.py (DAG, topological sort, backtracking "
         "solver, circular dependency detection).",
         "writes>=8", 240),
        ("Continue. Build registry.py, installer.py, checksum.py.",
         "writes>=11", 180),
        ("Continue. Build tree.py, conflict.py, builder.py.",
         "writes>=14", 180),
        ("Continue. Build venv.py, scaffold.py. Write ALL remaining files.",
         "writes>=17", 240),
        # ── Basic testing (5) ──
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        ("Test init: python3 -m {pkg} init test_project — verify "
         "crate.toml and src/ directory were created.",
         "msgs>={prev}+3", 120),
        ("Create a sample package in the registry. Create a .registry/ dir "
         "with a 'mathlib' package at version 1.0.0 with a manifest.json.",
         "msgs>={prev}+3", 150),
        ("Test install: cd test_project && python3 -m {pkg} install mathlib",
         "msgs>={prev}+3", 120),
        ("Test list: python3 -m {pkg} list — does it show mathlib?",
         "msgs>={prev}+2", 90),
        # ── Dependency resolution (4) ──
        ("Create a second package 'utils' at 1.0.0 that depends on "
         "mathlib>=1.0. Publish it to the registry.",
         "msgs>={prev}+3", 150),
        ("Install utils — it should also pull in mathlib as a dependency. "
         "Test: python3 -m {pkg} install utils",
         "msgs>={prev}+3", 120),
        ("Show the dependency tree: python3 -m {pkg} tree",
         "msgs>={prev}+2", 90),
        ("Test lock file generation: python3 -m {pkg} lock — read "
         "crate.lock and verify it lists both packages with checksums.",
         "msgs>={prev}+3", 120),
        # ── Version constraints (3) ──
        ("Read version.py. Walk me through how '^1.2.3' is parsed "
         "and matched against candidate versions.",
         "msgs>={prev}+2", 120),
        ("Test version constraint: create mathlib 2.0.0 and try installing "
         "with constraint mathlib@\">=1.0,<2.0\" — should get 1.0.0.",
         "msgs>={prev}+3", 150),
        ("Test conflict detection: create two packages that require "
         "incompatible versions of mathlib. Does it report the conflict?",
         "msgs>={prev}+3", 150),
        # ── Code review (2) ──
        ("Read resolver.py. How does the backtracking solver work? "
         "How does it handle diamond dependencies?",
         "msgs>={prev}+2", 120),
        ("Read manifest.py. How does the TOML parser handle nested "
         "tables like [dependencies]?",
         "msgs>={prev}+2", 90),
        # ── Advanced features (3) ──
        ("Test checksum verification: python3 -m {pkg} verify — "
         "does it detect tampered packages?",
         "msgs>={prev}+3", 120),
        ("Test uninstall: python3 -m {pkg} uninstall mathlib",
         "msgs>={prev}+2", 90),
        ("Test search: python3 -m {pkg} search math — does it "
         "find mathlib in the registry?",
         "msgs>={prev}+2", 90),
        # ── Bug hunt (2) ──
        ("What happens when you install a package that doesn't exist? "
         "Test: python3 -m {pkg} install nonexistent",
         "msgs>={prev}+2", 90),
        ("What about circular dependencies? Create A→B→A and test. "
         "Should report the cycle, not hang.",
         "msgs>={prev}+3", 120),
        # ── Wrap-up (1) ──
        ("Write a README.md with usage examples for all commands.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Web Framework — 30 steps (60-MIN TIER: HTTP, routing, ORM, templates)
    # ═══════════════════════════════════════════════════════════════
    "web_frame": [
        # ── Planning (2) ──
        ("Review the PRD. This is a micro web framework 'Spark' with ~20 "
         "files: HTTP server, router, middleware, templates, ORM with "
         "SQLite, sessions, migrations. Plan the build order.",
         "msgs>=3", 150),
        ("Create a todo list. Then start building — write __init__.py, "
         "cli.py, __main__.py, errors.py first. Do NOT stop.",
         "writes>=4", 180),
        # ── Core HTTP (5) ──
        ("Build request.py (parse method, path, headers, query, body, "
         "json, form, cookies) and response.py (status, headers, body, "
         "json, redirect, set_cookie).",
         "writes>=6", 240),
        ("Build router.py (path params like /users/<id>, method filtering) "
         "and app.py (App class, route decorator, middleware registration).",
         "writes>=8", 240),
        ("Build server.py (HTTP server on http.server + ThreadingMixIn) "
         "and middleware.py (pipeline + logging, CORS, timing).",
         "writes>=10", 240),
        ("Build template.py (variables, if/else, for loops, includes), "
         "static.py (file serving, mime types), forms.py.",
         "writes>=13", 240),
        ("Build sessions.py (cookie-based, file storage, expiry).",
         "writes>=14", 180),
        # ── ORM (3) ──
        ("Build orm/columns.py, orm/database.py (SQLite connection, "
         "parameterized queries), orm/model.py (Model base, CRUD).",
         "writes>=17", 240),
        ("Build orm/migration.py (up/down, version tracking) and "
         "orm/__init__.py.",
         "writes>=19", 180),
        ("Build scaffold.py. Verify ALL files are created.",
         "writes>=20", 180),
        # ── Testing (7) ──
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        ("Test init: python3 -m {pkg} init test_app — verify project "
         "structure was created.",
         "msgs>={prev}+3", 120),
        ("Test routes: python3 -m {pkg} routes — does it show registered routes?",
         "msgs>={prev}+2", 90),
        ("Create a simple app.py in test_app/ with 2 routes: / returns "
         "HTML, /api/hello returns JSON. Use the framework API.",
         "msgs>={prev}+3", 150),
        ("Start the server in background and test with curl or urllib. "
         "Does / return HTML? Does /api/hello return JSON?",
         "msgs>={prev}+4", 180),
        ("Create a User model and test ORM: create table, insert a user, "
         "query all users, verify with sqlite3.",
         "msgs>={prev}+4", 180),
        ("Test migrations: python3 -m {pkg} migrate create 'add_users' "
         "then python3 -m {pkg} migrate up.",
         "msgs>={prev}+3", 120),
        # ── Code review (3) ──
        ("Read router.py. How does path parameter extraction work? "
         "How does /users/<id> match /users/42?",
         "msgs>={prev}+2", 120),
        ("Read template.py. How are {% for %} and {% if %} parsed? "
         "Can they be nested?",
         "msgs>={prev}+2", 120),
        ("Read orm/model.py. How does .where() build the SQL query? "
         "Are queries parameterized against injection?",
         "msgs>={prev}+2", 120),
        # ── Bug hunt (3) ──
        ("What happens when requesting a non-existent route? Test a 404.",
         "msgs>={prev}+2", 90),
        ("What about a malformed JSON POST body? Does it crash or "
         "return a 400 error?",
         "msgs>={prev}+2", 90),
        ("What about an ORM query on a non-existent table?",
         "msgs>={prev}+2", 90),
        # ── Feature test (3) ──
        ("Test the template engine: create a template with {{ var }} "
         "and {% for item in items %}, render it, verify output HTML.",
         "msgs>={prev}+3", 150),
        ("Test static file serving: put a test.txt in test_app/static/, "
         "request /static/test.txt via urllib.",
         "msgs>={prev}+3", 120),
        ("Test sessions: create a login route that sets session, and a "
         "dashboard route that reads it.",
         "msgs>={prev}+3", 150),
        # ── Wrap-up (2) ──
        ("List all files in web_frame/. How many total? Show the "
         "final directory tree.",
         "msgs>={prev}+2", 90),
        ("Write a README.md with API usage examples.",
         "done", 120),
    ],

    # ═══════════════════════════════════════════════════════════════
    # Build System — 30 steps (60-MIN TIER: graph, cache, parallel, plugins)
    # ═══════════════════════════════════════════════════════════════
    "build_sys": [
        # ── Planning (2) ──
        ("Review the PRD. This is a build system 'Forge' with ~18 files: "
         "dependency graph, parallel scheduler, content-addressable cache, "
         "plugins, workspaces. Plan the architecture.",
         "msgs>=3", 150),
        ("Create a todo list. Then start building — write __init__.py, "
         "cli.py, __main__.py, errors.py first. Do NOT stop.",
         "writes>=4", 180),
        # ── Core build (6) ──
        ("Build config.py (TOML-like parser for forge.toml, handles "
         "tables, arrays, profiles) and targets.py (target definitions).",
         "writes>=6", 240),
        ("Build graph.py (DAG construction, cycle detection, topological "
         "sort) and glob_utils.py (pattern expansion).",
         "writes>=8", 240),
        ("Build executor.py (run commands, variable substitution) and "
         "cache.py (content-addressable, SHA-256, mtime tracking).",
         "writes>=10", 240),
        ("Build scheduler.py (ThreadPoolExecutor, dependency-aware task "
         "scheduling) and reporter.py (progress, timing, errors).",
         "writes>=12", 240),
        ("Build profiles.py, plugins.py, workspace.py.",
         "writes>=15", 240),
        ("Build watcher.py, artifacts.py, scaffold.py. Write ALL files.",
         "writes>=18", 240),
        # ── Testing (6) ──
        ("Run python3 -m {pkg} --help and fix any errors.",
         "msgs>={prev}+3", 120),
        ("Create a sample project with a forge.toml that has 3 targets: "
         "lib, app (depends on lib), and test (depends on lib). Use "
         "simple 'echo' or 'cp' commands.",
         "msgs>={prev}+3", 150),
        ("Test build: python3 -m {pkg} build — does it build in "
         "correct dependency order?",
         "msgs>={prev}+3", 120),
        ("Test targets: python3 -m {pkg} targets — list all targets.",
         "msgs>={prev}+2", 90),
        ("Test graph: python3 -m {pkg} graph — show the dependency "
         "graph as ASCII art.",
         "msgs>={prev}+2", 90),
        ("Test incremental: run build again — are targets cached?",
         "msgs>={prev}+3", 120),
        # ── Advanced (5) ──
        ("Test dry-run: python3 -m {pkg} build --dry-run — does it "
         "show what would build without executing?",
         "msgs>={prev}+2", 90),
        ("Test clean: python3 -m {pkg} clean — removes build artifacts.",
         "msgs>={prev}+2", 90),
        ("Test cache stats: python3 -m {pkg} cache stats — show "
         "hit/miss ratio.",
         "msgs>={prev}+2", 90),
        ("Test parallel: python3 -m {pkg} build --parallel 2 — does "
         "it show parallel execution progress?",
         "msgs>={prev}+3", 120),
        ("Test profiles: add a [profiles.release] section to forge.toml "
         "and build with --profile release.",
         "msgs>={prev}+3", 150),
        # ── Code review (3) ──
        ("Read graph.py. How does cycle detection work? What algorithm?",
         "msgs>={prev}+2", 120),
        ("Read cache.py. How is the content-addressable cache organized? "
         "SHA-256 of what exactly?",
         "msgs>={prev}+2", 120),
        ("Read scheduler.py. How does it know which targets can run "
         "in parallel vs must wait for deps?",
         "msgs>={prev}+2", 120),
        # ── Bug hunt (3) ──
        ("Create a circular dependency (A→B→A) and build. Does it "
         "detect the cycle and report it?",
         "msgs>={prev}+3", 120),
        ("What about a target with a missing dependency? Test it.",
         "msgs>={prev}+2", 90),
        ("What about a command that fails (exit code 1)? Does it "
         "report which target failed and stop?",
         "msgs>={prev}+2", 90),
        # ── Wrap-up (3) ──
        ("Run a final full build with --parallel 2. Show timing output.",
         "msgs>={prev}+3", 150),
        ("List all files in build_sys/. How many total?",
         "msgs>={prev}+2", 90),
        ("Write a README.md with usage examples.",
         "done", 120),
    ],
}

# Default fallback script for unknown packages
DEFAULT_SCRIPT: list[tuple[str, str, int]] = [
    ("Review the PRD and create a plan. List what files you need.",
     "msgs>=3", 120),
    ("Create a todo list and start building. Execute all items.",
     "writes>=3", 150),
    ("Continue — write the remaining files.",
     "writes>=5", 150),
    ("Test: python3 -m {pkg} --help",
     "msgs>={prev}+2", 90),
    ("Test the main functionality.",
     "msgs>={prev}+3", 120),
    ("Add a new feature using search_replace.",
     "msgs>={prev}+4", 120),
    ("Test the new feature.",
     "msgs>={prev}+2", 90),
    ("What ideas do you have for improvements?",
     "msgs>={prev}+2", 90),
    ("Run all tests one final time and show results.",
     "msgs>={prev}+3", 150),
    ("Write a README.md.",
     "done", 120),
]


# ── Autonomous bonus round: single mega-prompt, model must self-manage ──
# These test whether the model can execute a long checklist without
# stopping to ask the user "shall I continue?" after each item.
AUTONOMOUS_SCRIPTS: dict[str, list[tuple[str, str, int]]] = {
    "tool_agent": [
        ("I need you to do ALL of the following without stopping or asking "
         "me anything. Execute every item, then show me the results:\n\n"
         "CRITICAL: Write __init__.py, __main__.py, and cli.py FIRST before "
         "any other module. The package MUST be importable before you test it.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write tool_agent/__init__.py\n"
         "4. Write tool_agent/cli.py (argument parsing)\n"
         "5. Write tool_agent/__main__.py (imports from cli)\n"
         "6. Write tool_agent/tools.py (6 built-in tools)\n"
         "7. Write tool_agent/agent.py (multi-step planner)\n"
         "8. Write tool_agent/parser.py (pipe syntax)\n"
         "9. Write tool_agent/plugins.py and tool_agent/memory.py\n"
         "10. Run python3 -m tool_agent --help — FIX ANY ERRORS before continuing\n"
         "11. Test: python3 -m tool_agent \"What is 99*77?\"\n"
         "12. Test: python3 -m tool_agent \"Read PRD.md\"\n"
         "13. Create plugins/reverse_tool.py\n"
         "14. Add a 'date_tool' using search_replace on tools.py\n"
         "15. Write a README.md\n"
         "16. Mark all todos done\n\n"
         "DO NOT STOP between items. Do all 16.",
         "writes>=9,pkg_works", 600),
    ],
    "stock_screener": [
        ("Execute this entire checklist without stopping:\n\n"
         "CRITICAL: Write __init__.py, __main__.py, and cli.py FIRST.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write stock_screener/__init__.py\n"
         "4. Write stock_screener/cli.py\n"
         "5. Write stock_screener/__main__.py\n"
         "6. Write stock_screener/data.py, screener.py, sectors.py\n"
         "7. Write stock_screener/watchlist.py, compare.py, formatter.py\n"
         "8. Create portfolio.csv with 15 stocks across 4 sectors\n"
         "9. Run python3 -m stock_screener --help — FIX ANY ERRORS\n"
         "10. Test: python3 -m stock_screener screen --data portfolio.csv --pb-max 1.5\n"
         "11. Test: python3 -m stock_screener rank --data portfolio.csv --top 5\n"
         "12. Test: python3 -m stock_screener sectors --data portfolio.csv\n"
         "13. Test: python3 -m stock_screener summary --data portfolio.csv\n"
         "14. Write a README.md\n"
         "15. Mark all todos done\n\n"
         "Do ALL items without asking me anything.",
         "writes>=10,pkg_works", 600),
    ],
    "eval_harness": [
        ("Execute this entire checklist without stopping:\n\n"
         "CRITICAL: Write __init__.py, __main__.py, and cli.py FIRST.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write eval_harness/__init__.py\n"
         "4. Write eval_harness/cli.py\n"
         "5. Write eval_harness/__main__.py\n"
         "6. Write eval_harness/evaluators.py (exact, fuzzy, contains, regex, "
         "code, normalize, numeric + pipeline chaining)\n"
         "7. Write eval_harness/runner.py, stats.py, diff.py, report.py\n"
         "8. Create tasks.json with 20 test cases across 4 categories\n"
         "9. Run python3 -m eval_harness --help — FIX ANY ERRORS\n"
         "10. Run: python3 -m eval_harness run --dataset tasks.json --evaluator exact\n"
         "11. Run: python3 -m eval_harness run --dataset tasks.json --evaluator \"normalize,exact\"\n"
         "12. Run: python3 -m eval_harness stats --results results.json\n"
         "13. Write a README.md\n"
         "14. Mark all todos done\n\n"
         "Do ALL items without asking me anything.",
         "writes>=9,pkg_works", 600),
    ],
    "doc_qa": [
        ("Execute this entire checklist without stopping. "
         "IMPORTANT: the package name is doc_qa — create files under doc_qa/ directory.\n\n"
         "CRITICAL: Write __init__.py, __main__.py, and cli.py FIRST.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write doc_qa/__init__.py\n"
         "4. Write doc_qa/cli.py (argument parsing for all subcommands)\n"
         "5. Write doc_qa/__main__.py (imports from cli)\n"
         "6. Write doc_qa/ingest.py with chunking and SHA-256 file hashing\n"
         "7. Write doc_qa/tfidf.py and doc_qa/bm25.py\n"
         "8. Write doc_qa/query.py and doc_qa/storage.py\n"
         "9. Create test_docs/ with 3 text files about different topics\n"
         "10. Run python3 -m doc_qa --help — FIX ANY ERRORS\n"
         "11. Run: python3 -m doc_qa ingest test_docs/\n"
         "12. Run: python3 -m doc_qa query \"What is Python?\" --algorithm bm25\n"
         "13. Run: python3 -m doc_qa stats\n"
         "14. Write a README.md\n"
         "15. Mark all todos done\n\n"
         "Do ALL items without asking me anything.",
         "writes>=9,pkg_works", 600),
    ],
    "prompt_optimizer": [
        ("Execute this entire checklist without stopping:\n\n"
         "CRITICAL: Write __init__.py, __main__.py, and cli.py FIRST.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write prompt_optimizer/__init__.py\n"
         "4. Write prompt_optimizer/cli.py\n"
         "5. Write prompt_optimizer/__main__.py\n"
         "6. Write prompt_optimizer/templates.py (5 mutation strategies + crossover)\n"
         "7. Write prompt_optimizer/executor.py (keyword classifier + negation)\n"
         "8. Write prompt_optimizer/scorer.py, optimizer.py, history.py\n"
         "9. Create data.json with 20 sentiment examples\n"
         "10. Run python3 -m prompt_optimizer --help — FIX ANY ERRORS\n"
         "11. Run: python3 -m prompt_optimizer run --task sentiment --dataset data.json "
         "--iterations 5 --population 6\n"
         "12. Show best: python3 -m prompt_optimizer best --task sentiment\n"
         "13. Write a README.md\n"
         "14. Mark all todos done\n\n"
         "Do ALL items without asking me anything.",
         "writes>=9,pkg_works", 600),
    ],
    # ── 15-minute tier ──
    "mini_db": [
        ("Execute this entire checklist without stopping.\n"
         "CRITICAL: Write __init__.py, cli.py, __main__.py FIRST.\n"
         "This is a LARGE project (13 files). Do not stop early.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write mini_db/__init__.py, mini_db/cli.py, mini_db/__main__.py\n"
         "4. Write mini_db/types.py (INT, FLOAT, TEXT, BOOL)\n"
         "5. Write mini_db/parser.py (SQL tokenizer + recursive descent, NO eval)\n"
         "6. Write mini_db/where.py (comparison ops, AND/OR, LIKE)\n"
         "7. Write mini_db/table.py (CRUD, schema validation)\n"
         "8. Write mini_db/storage.py (JSON persistence)\n"
         "9. Write mini_db/engine.py (query execution)\n"
         "10. Write mini_db/formatter.py (ASCII table + CSV)\n"
         "11. Write mini_db/index.py (B-tree)\n"
         "12. Write mini_db/transaction.py (WAL)\n"
         "13. Write mini_db/join.py\n"
         "14. Run python3 -m mini_db --help — FIX ANY ERRORS\n"
         "15. Test: python3 -m mini_db exec \"CREATE TABLE users (id INT, name TEXT, age INT)\"\n"
         "16. Test: python3 -m mini_db exec \"INSERT INTO users VALUES (1, 'Alice', 30)\"\n"
         "17. Test: python3 -m mini_db exec \"SELECT * FROM users\"\n"
         "18. Test: python3 -m mini_db exec \"SELECT * FROM users WHERE age > 25\"\n"
         "19. Write a README.md\n"
         "20. Mark all todos done\n\n"
         "Do ALL 20 items without asking me anything.",
         "writes>=14,pkg_works", 900),
    ],
    "site_gen": [
        ("Execute this entire checklist without stopping.\n"
         "CRITICAL: Write __init__.py, cli.py, __main__.py FIRST.\n"
         "This is a LARGE project (15 files). Do not stop early.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write site_gen/__init__.py, site_gen/cli.py, site_gen/__main__.py\n"
         "4. Write site_gen/config.py (TOML-like parser)\n"
         "5. Write site_gen/frontmatter.py (key:value metadata)\n"
         "6. Write site_gen/markdown.py (full MD-to-HTML, NO markdown library)\n"
         "7. Write site_gen/templates.py (variable substitution + inheritance)\n"
         "8. Write site_gen/plugins.py (TOC, reading time, word count, slug)\n"
         "9. Write site_gen/navigation.py (auto-nav from file tree)\n"
         "10. Write site_gen/builder.py (main build pipeline)\n"
         "11. Write site_gen/sitemap.py and site_gen/feed.py\n"
         "12. Write site_gen/server.py, site_gen/watcher.py, site_gen/scaffold.py\n"
         "13. Run python3 -m site_gen --help — FIX ANY ERRORS\n"
         "14. Test: python3 -m site_gen init test_site\n"
         "15. Create 3 test Markdown files with front matter in test_site/content/\n"
         "16. Test: python3 -m site_gen build --source test_site/content --output test_site/dist\n"
         "17. Verify test_site/dist/index.html exists and has valid HTML\n"
         "18. Write a README.md\n"
         "19. Mark all todos done\n\n"
         "Do ALL 19 items without asking me anything.",
         "writes>=15,pkg_works", 900),
    ],
    # ── 30-minute tier ──
    "lang_interp": [
        ("Execute this entire checklist without stopping.\n"
         "CRITICAL: Write __init__.py, cli.py, __main__.py FIRST.\n"
         "This is a VERY LARGE project (16 files). Do not stop early.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write lang_interp/__init__.py, cli.py, __main__.py\n"
         "4. Write lang_interp/tokens.py and lang_interp/errors.py\n"
         "5. Write lang_interp/lexer.py (tokenizer)\n"
         "6. Write lang_interp/ast_nodes.py (AST node definitions)\n"
         "7. Write lang_interp/parser.py (recursive descent)\n"
         "8. Write lang_interp/types.py (BoltInt, BoltStr, BoltList, etc)\n"
         "9. Write lang_interp/environment.py (scopes, closures)\n"
         "10. Write lang_interp/interpreter.py (tree-walking eval)\n"
         "11. Write lang_interp/stdlib.py (print, len, range, etc)\n"
         "12. Write lang_interp/builtins.py (methods on types)\n"
         "13. Write lang_interp/checker.py (type checker)\n"
         "14. Write lang_interp/repl.py and lang_interp/module_loader.py\n"
         "15. Run python3 -m lang_interp --help — FIX ANY ERRORS\n"
         "16. Create test.bolt: let x = 42; print(x * 2)\n"
         "17. Run: python3 -m lang_interp run test.bolt\n"
         "18. Create test2.bolt with fn add(a,b) { return a+b }; print(add(3,4))\n"
         "19. Run: python3 -m lang_interp run test2.bolt\n"
         "20. Write a README.md\n"
         "21. Mark all todos done\n\n"
         "Do ALL 21 items without asking me anything.",
         "writes>=16,pkg_works", 1200),
    ],
    "pkg_manager": [
        ("Execute this entire checklist without stopping.\n"
         "CRITICAL: Write __init__.py, cli.py, __main__.py FIRST.\n"
         "This is a VERY LARGE project (17 files). Do not stop early.\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write pkg_manager/__init__.py, cli.py, __main__.py\n"
         "4. Write pkg_manager/errors.py and pkg_manager/config.py\n"
         "5. Write pkg_manager/version.py (semver parsing + constraints)\n"
         "6. Write pkg_manager/manifest.py (TOML-like parser)\n"
         "7. Write pkg_manager/lockfile.py\n"
         "8. Write pkg_manager/resolver.py (DAG, topological sort, backtracking)\n"
         "9. Write pkg_manager/registry.py and pkg_manager/checksum.py\n"
         "10. Write pkg_manager/installer.py\n"
         "11. Write pkg_manager/tree.py, conflict.py, builder.py\n"
         "12. Write pkg_manager/venv.py, scaffold.py\n"
         "13. Run python3 -m pkg_manager --help — FIX ANY ERRORS\n"
         "14. Test: python3 -m pkg_manager init test_project\n"
         "15. Test: python3 -m pkg_manager list\n"
         "16. Write a README.md\n"
         "17. Mark all todos done\n\n"
         "Do ALL 17 items without asking me anything.",
         "writes>=17,pkg_works", 1200),
    ],
    # ── 60-minute tier ──
    "web_frame": [
        ("Execute this entire checklist without stopping.\n"
         "CRITICAL: Write __init__.py, cli.py, __main__.py FIRST.\n"
         "This is a VERY LARGE project (~20 files including orm/ subpackage).\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write web_frame/__init__.py, cli.py, __main__.py, errors.py\n"
         "4. Write web_frame/request.py and web_frame/response.py\n"
         "5. Write web_frame/router.py (path params, method filtering)\n"
         "6. Write web_frame/app.py (App class, route decorator)\n"
         "7. Write web_frame/server.py (HTTP on http.server + threading)\n"
         "8. Write web_frame/middleware.py (pipeline + builtins)\n"
         "9. Write web_frame/template.py (vars, if/else, for, includes)\n"
         "10. Write web_frame/static.py, forms.py, sessions.py\n"
         "11. Write web_frame/orm/__init__.py, orm/columns.py\n"
         "12. Write web_frame/orm/database.py, orm/model.py, orm/migration.py\n"
         "13. Write web_frame/scaffold.py\n"
         "14. Run python3 -m web_frame --help — FIX ANY ERRORS\n"
         "15. Test: python3 -m web_frame init test_app\n"
         "16. Test: python3 -m web_frame routes\n"
         "17. Write a README.md\n"
         "18. Mark all todos done\n\n"
         "Do ALL 18 items without asking me anything.",
         "writes>=20,pkg_works", 1800),
    ],
    "build_sys": [
        ("Execute this entire checklist without stopping.\n"
         "CRITICAL: Write __init__.py, cli.py, __main__.py FIRST.\n"
         "This is a VERY LARGE project (~18 files).\n\n"
         "1. Read the PRD\n"
         "2. Create a todo list\n"
         "3. Write build_sys/__init__.py, cli.py, __main__.py, errors.py\n"
         "4. Write build_sys/config.py (TOML parser for forge.toml)\n"
         "5. Write build_sys/targets.py and build_sys/glob_utils.py\n"
         "6. Write build_sys/graph.py (DAG, cycle detection, topo sort)\n"
         "7. Write build_sys/executor.py (run commands, var substitution)\n"
         "8. Write build_sys/cache.py (SHA-256, content-addressable)\n"
         "9. Write build_sys/scheduler.py (ThreadPoolExecutor)\n"
         "10. Write build_sys/reporter.py (progress, timing)\n"
         "11. Write build_sys/profiles.py, plugins.py, workspace.py\n"
         "12. Write build_sys/watcher.py, artifacts.py, scaffold.py\n"
         "13. Run python3 -m build_sys --help — FIX ANY ERRORS\n"
         "14. Create a sample forge.toml with 3 targets\n"
         "15. Test: python3 -m build_sys build\n"
         "16. Test: python3 -m build_sys targets\n"
         "17. Write a README.md\n"
         "18. Mark all todos done\n\n"
         "Do ALL 18 items without asking me anything.",
         "writes>=18,pkg_works", 1800),
    ],
}


class SessionWatcher:
    """Lightweight session log poller.

    Earlier version re-read the entire `messages.jsonl` file on every
    refresh — on long stress runs (1k+ prompts, 36h+ runtimes) this
    accumulated into ~130 MB/hour of harness RSS growth because
    Python couldn't always free the replaced list fast enough and
    pexpect's poll loop slowed down with GC pressure, which made the
    harness miss the TUI's idle windows and retry/skip prompts that
    drydock was actually handling fine. Fix: keep a byte offset per
    file, read only the new tail each refresh, and cap the retained
    message window.

    Keeps only MAX_KEPT_MSGS most recent messages in memory. Callers
    that need older messages should scan the file directly.
    """

    MAX_KEPT_MSGS = 500

    def __init__(self, cwd: Path, since: float):
        self.cwd = cwd.resolve()
        self.since = since
        self.session_dir: Path | None = None
        self.messages: list[dict] = []
        # Per-file byte offsets: {path_str: last_read_byte}
        self._offsets: dict[str, int] = {}

    def find_session(self) -> Path | None:
        if self.session_dir is not None:
            return self.session_dir
        for entry in sorted(SESSION_ROOT.iterdir(), reverse=True):
            try:
                if not entry.is_dir():
                    continue
                if entry.stat().st_mtime < self.since - 5:
                    continue
                meta = json.loads((entry / "meta.json").read_text())
                wd = meta.get("environment", {}).get("working_directory", "")
                if str(self.cwd) == wd:
                    self.session_dir = entry
                    return entry
            except Exception:
                continue
        return None

    def _reset_offsets(self) -> None:
        """Called when the session dir changes — clear everything."""
        self._offsets.clear()
        self.messages.clear()

    def refresh(self) -> int:
        sd = self.find_session()
        if sd is None:
            return 0
        new_msgs = False
        for msg_file in sorted(sd.rglob("messages.jsonl")):
            key = str(msg_file)
            try:
                with msg_file.open("rb") as f:
                    f.seek(self._offsets.get(key, 0))
                    chunk = f.read()
                    self._offsets[key] = f.tell()
                if not chunk:
                    continue
                for raw in chunk.decode("utf-8", errors="replace").split("\n"):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        self.messages.append(json.loads(line))
                        new_msgs = True
                    except Exception:
                        continue
            except Exception:
                continue
        # Cap the in-memory window so long sessions don't bloat RAM.
        if len(self.messages) > self.MAX_KEPT_MSGS:
            self.messages = self.messages[-self.MAX_KEPT_MSGS:]
        if new_msgs:
            pass  # (noop; kept for potential telemetry hook later)
        return len(self.messages)

    def count_writes(self) -> int:
        n = 0
        for m in self.messages:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    if name in ("write_file", "search_replace"):
                        n += 1
                    elif name == "bash":
                        # Gemma 4 sometimes pivots to `cat <<EOF > file` or
                        # `echo ... > file` for file creation instead of
                        # write_file. Count these as writes too so the
                        # stress harness doesn't report false "0 writes".
                        try:
                            import json as _j
                            cmd = _j.loads(fn.get("arguments", "") or "{}").get("command", "")
                        except Exception:
                            cmd = ""
                        if (">" in cmd and ("cat <<" in cmd or "echo " in cmd
                                            or "printf " in cmd or "tee " in cmd)):
                            n += 1
        return n

    def has_errors(self) -> list[str]:
        errors = []
        for m in self.messages:
            if m.get("role") == "tool":
                c = str(m.get("content", ""))
                if "<tool_error>" in c:
                    errors.append(c[:120])
        return errors

    def model_said_done(self) -> bool:
        if not self.messages:
            return False
        last = self.messages[-1]
        if last.get("role") == "assistant":
            tc = last.get("tool_calls", [])
            content = last.get("content", "") or ""
            if not tc and content.strip():
                return True
        return False

    def pkg_works(self, pkg: str) -> bool:
        """Check if python3 -m pkg --help succeeds from cwd.

        WARNING: This is NOT a real test. --help success proves nothing about
        whether the code actually works. Use functional_pass() for real testing.
        Kept for backwards compatibility with old scripts.
        """
        try:
            r = subprocess.run(
                ["python3", "-m", pkg, "--help"],
                cwd=str(self.cwd), capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0 and len(r.stdout.strip()) > 0
        except Exception:
            return False

    def tui_functional_result(self) -> tuple[str, str]:
        """Parse the session log to find the TUI's most recent functional
        test run result. Returns (status, details):
          status = "PASS" if TUI saw 'X passed, 0 failed' in a tool result
          status = "FAIL" if TUI saw '..., N failed' with N>0
          status = "NOT_RUN" if no functional_tests.sh execution was found
        This is what the TUI actually observed — the ONLY real signal.
        """
        last_result = None
        for m in self.messages:
            if m.get("role") != "tool":
                continue
            content = str(m.get("content", ""))
            # Look for the functional_tests.sh result marker
            match = re.search(r"RESULT:\s*(\d+)\s*passed,\s*(\d+)\s*failed", content)
            if match:
                p, f = int(match.group(1)), int(match.group(2))
                last_result = (p, f, content[-500:])
        if last_result is None:
            return "NOT_RUN", "TUI never ran functional_tests.sh"
        p, f, tail = last_result
        if f == 0 and p > 0:
            return "PASS", f"TUI saw: {p} passed, 0 failed\n{tail}"
        return "FAIL", f"TUI saw: {p} passed, {f} failed\n{tail}"


def type_message(child: pexpect.spawn, text: str) -> None:
    """Type a prompt into the TUI. Does NOT wait for it to be processed.
    Use confirm_user_message_accepted() afterward to verify delivery."""
    for ch in text:
        child.send(ch)
        time.sleep(0.01)
    time.sleep(0.2)
    child.send("\r")


def count_user_messages(watcher: "SessionWatcher") -> int:
    """Count user-role messages in the session log."""
    return sum(1 for m in watcher.messages if m.get("role") == "user")


def send_prompt_and_confirm(child: pexpect.spawn, text: str,
                            watcher: "SessionWatcher",
                            max_retries: int = 3,
                            wait_per_retry: float = 30.0) -> bool:
    """Type prompt, wait until a new user message appears in the session log.
    If no new user message in wait_per_retry seconds, retype. Returns True
    if the prompt was accepted by the TUI."""
    initial_user_count = count_user_messages(watcher)
    for attempt in range(max_retries):
        type_message(child, text)
        deadline = time.time() + wait_per_retry
        while time.time() < deadline:
            drain_pty(child, seconds=1.0)
            watcher.refresh()
            if count_user_messages(watcher) > initial_user_count:
                return True
            time.sleep(0.5)
        if attempt < max_retries - 1:
            print(f"  [retry {attempt + 1}: prompt not accepted, retyping]")
            # Send a stray Enter in case TUI was waiting on a modal
            child.send("\r")
            time.sleep(1)
    return False


def drain_pty(child: pexpect.spawn, seconds: float = 2.0) -> None:
    """Read PTY output to prevent buffer deadlock.

    Pexpect accumulates every read byte in `child.buffer` and `child.before`
    until a pattern matches. We only ever expect TIMEOUT here, so nothing
    matches, and those two attributes grow without bound for the life of
    the harness. On 48h stress runs that was ~1GB/day of RSS growth — the
    second half of the bloat that c541ed9 did not catch. The PTY is also
    mirrored to `child.logfile_read` on disk, so truncating the in-memory
    copy loses nothing.
    """
    cycles = int(seconds / 0.1)
    for _ in range(cycles):
        try:
            child.expect(pexpect.TIMEOUT, timeout=0.1)
        except pexpect.EOF:
            break
    _trim_pexpect_buffers(child)


_PEXPECT_BUFFER_TAIL = 4096


def _trim_pexpect_buffers(child: pexpect.spawn) -> None:
    """Cap pexpect's in-memory buffers to the last 4KB.

    `child.before` / `child.buffer` are plain strings; pexpect tolerates
    callers slicing them. The only place this harness reads `.before` is
    the one-shot Trust-folder check at startup, which runs before the
    first drain_pty call, so tail-truncation is safe.
    """
    try:
        before = getattr(child, "before", None)
        if isinstance(before, str) and len(before) > _PEXPECT_BUFFER_TAIL:
            child.before = before[-_PEXPECT_BUFFER_TAIL:]
        buf = getattr(child, "buffer", None)
        if isinstance(buf, str) and len(buf) > _PEXPECT_BUFFER_TAIL:
            child.buffer = buf[-_PEXPECT_BUFFER_TAIL:]
    except Exception:
        pass


def check_condition(cond: str, watcher: SessionWatcher, prev_msgs: int,
                    pkg: str = "") -> bool:
    cond = cond.replace("{prev}", str(prev_msgs))
    if cond == "done":
        return watcher.model_said_done()
    if cond == "pkg_works":
        return watcher.pkg_works(pkg)
    # Compound: "writes>=9,pkg_works" — both must be true
    if "," in cond:
        return all(check_condition(c.strip(), watcher, prev_msgs, pkg)
                   for c in cond.split(","))
    m = re.match(r"(msgs|writes|tool_calls)([><=]+)(\d+)", cond)
    if not m:
        return False
    metric, op, val = m.group(1), m.group(2), int(m.group(3))
    if metric == "msgs":
        actual = len(watcher.messages)
    elif metric == "writes":
        actual = watcher.count_writes()
    else:
        actual = len(watcher.messages)  # fallback
    if op == ">=":
        return actual >= val
    if op == ">":
        return actual > val
    return actual == val


def run_interactive(cwd: Path, pkg: str) -> int:
    print(f"\n{'='*60}")
    print(f"  INTERACTIVE SHAKEDOWN: {pkg}")
    print(f"  cwd: {cwd}")
    print(f"{'='*60}\n")

    # Clean up
    master = cwd / "PRD.master.md"
    target = cwd / "PRD.md"
    if master.exists():
        shutil.copy2(master, target)
    pkg_dir = cwd / pkg
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    # Remove stale dirs that the model might have created with wrong names
    # (e.g., doc_qa_rag instead of doc_qa, or tool-agent instead of tool_agent)
    for entry in cwd.iterdir():
        if entry.is_dir() and entry.name != pkg and entry.name.startswith(pkg.split("_")[0]):
            if entry.name not in ("test_docs", "test_data", "plugins", ".git"):
                print(f"  Removing stale dir: {entry.name}/")
                shutil.rmtree(entry)
    # Also remove storage dirs from previous runs
    for dotdir in [f".{pkg}", ".doc_qa", ".tool_agent", ".stock_screener",
                   ".prompt_optimizer", ".eval_harness"]:
        dd = cwd / dotdir
        if dd.exists():
            shutil.rmtree(dd)

    log_path = Path(f"/tmp/shakedown_interactive_{int(time.time())}.tui.log")
    start_time = time.time()

    env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"}
    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "w")
    child.expect([r">", r"Drydock", r"┌"], timeout=30)
    time.sleep(2)

    # Dismiss trust dialog if present
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.2)
            child.send("\r")
            time.sleep(2)
    except Exception:
        pass

    watcher = SessionWatcher(cwd, since=start_time)
    results: list[dict] = []

    # Pick the conversation script for this package
    mode = os.environ.get("SHAKEDOWN_MODE", "interactive")
    if mode == "autonomous" and pkg in AUTONOMOUS_SCRIPTS:
        script = list(AUTONOMOUS_SCRIPTS[pkg])
        print(f"  Mode: AUTONOMOUS (1 mega-prompt, model must self-manage)")
    else:
        script = list(SCRIPTS.get(pkg, DEFAULT_SCRIPT))
        print(f"  Mode: interactive ({len(script)} steps)")

    # ── Inject functional test iteration phase ──────────────────────
    # This is the REAL test loop. The TUI runs functional_tests.sh via
    # its bash tool, observes failures, reads the failing code, fixes
    # it with search_replace, and iterates. The harness only observes.
    # We NEVER run the test from outside — that would bypass the agent.
    ft_path = cwd / "functional_tests.sh"
    if ft_path.exists():
        iteration_steps = [
            ("There is a functional_tests.sh file in this directory. Run it now: "
             "bash functional_tests.sh — and show me the full output. This is "
             "the REAL test of whether your code works. The test file runs actual "
             "feature commands and checks their output. Report every PASS and FAIL "
             "line, and the final 'RESULT: X passed, Y failed' line.",
             "msgs>={prev}+3", 180),
            ("For EACH failing test: read the relevant source file, identify "
             "the bug (wrong output, missing feature, crash), and fix it using "
             "search_replace or write_file. Then run 'bash functional_tests.sh' "
             "AGAIN and report the new result. Keep iterating until all tests "
             "pass or you cannot fix a specific failure.",
             "msgs>={prev}+6", 600),
            ("Run bash functional_tests.sh ONE MORE TIME and show me the final "
             "'RESULT: X passed, Y failed' line. If there are still failures, "
             "do another round of fixes. Your goal is 0 failed.",
             "msgs>={prev}+4", 400),
            ("Final report: state clearly how many tests pass and how many fail. "
             "For any that still fail, explain the root cause you identified "
             "and why you could or could not fix it.",
             "msgs>={prev}+2", 180),
        ]
        script = script + iteration_steps
        print(f"  + {len(iteration_steps)} TUI-driven functional test iteration steps")
    else:
        print(f"  WARNING: no functional_tests.sh — this run will be UNTESTED")

    try:
        for step_idx, (prompt_template, condition, max_wait) in enumerate(script):
            prompt = prompt_template.replace("{pkg}", pkg)

            print(f"\n--- Step {step_idx + 1}/{len(script)} ---")
            print(f"  PROMPT: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
            print(f"  WAIT:   {condition} (max {max_wait}s)")

            # On first step, wait for the session to appear before we can
            # confirm user messages.
            if step_idx == 0:
                type_message(child, prompt)
                time.sleep(1)
                print("  Waiting for session to appear...", end="", flush=True)
                for _ in range(30):
                    drain_pty(child)
                    if watcher.find_session():
                        break
                if watcher.find_session():
                    print(f" found: {watcher.session_dir.name}")
                else:
                    print(" not found (continuing anyway)")
                # For step 0 we can't confirm before typing, but check after
                watcher.refresh()
                if count_user_messages(watcher) == 0:
                    # Prompt didn't land — retype once
                    print("  [step 0 prompt not in log — retyping]")
                    type_message(child, prompt)
                    time.sleep(3)
                    watcher.refresh()
            else:
                # For all other steps: wait for TUI to actually accept the prompt
                accepted = send_prompt_and_confirm(child, prompt, watcher,
                                                    max_retries=3,
                                                    wait_per_retry=30.0)
                if not accepted:
                    print(f"  WARN: prompt not accepted after 3 retries — skipping step")
                    results.append({
                        "step": step_idx + 1,
                        "prompt": prompt[:80],
                        "condition": condition,
                        "met": False,
                        "elapsed": 0,
                        "msgs_after": len(watcher.messages),
                        "writes_after": watcher.count_writes(),
                        "errors": ["PROMPT NOT ACCEPTED (TUI busy or crashed)"],
                    })
                    continue

            prev_msgs = len(watcher.messages)

            # Wait for condition
            step_start = time.time()
            met = False
            last_report = 0
            while time.time() - step_start < max_wait:
                drain_pty(child)
                if not child.isalive():
                    print(f"  [TUI exited]")
                    break

                watcher.refresh()
                elapsed = int(time.time() - step_start)

                if elapsed - last_report >= 10:
                    msgs = len(watcher.messages)
                    writes = watcher.count_writes()
                    errors = len(watcher.has_errors())
                    print(f"  [{elapsed:3d}s] msgs={msgs} writes={writes} errors={errors}")
                    last_report = elapsed

                if check_condition(condition, watcher, prev_msgs, pkg):
                    met = True
                    elapsed = int(time.time() - step_start)
                    print(f"  [{elapsed:3d}s] Condition met! Waiting for TUI idle...")
                    # Wait for TUI to actually finish responding before
                    # moving on. "Idle" = no new messages for 6 seconds.
                    # This prevents typing the next prompt while TUI is busy.
                    idle_deadline = time.time() + 30
                    last_count = len(watcher.messages)
                    stable_since = time.time()
                    while time.time() < idle_deadline:
                        drain_pty(child, seconds=0.5)
                        watcher.refresh()
                        now_count = len(watcher.messages)
                        if now_count != last_count:
                            last_count = now_count
                            stable_since = time.time()
                        elif time.time() - stable_since >= 6:
                            break
                        time.sleep(0.5)
                    break

                # Check for dead silence (model not responding at all)
                if elapsed > 120 and len(watcher.messages) == prev_msgs:
                    print(f"  [{elapsed:3d}s] Dead silence — model not responding")
                    break

            step_result = {
                "step": step_idx + 1,
                "prompt": prompt[:80],
                "condition": condition,
                "met": met,
                "elapsed": int(time.time() - step_start),
                "msgs_after": len(watcher.messages),
                "writes_after": watcher.count_writes(),
                "errors": watcher.has_errors()[-3:],  # last 3 errors
            }
            results.append(step_result)

            if not met:
                print(f"  TIMEOUT: condition not met after {max_wait}s")
                # Continue anyway — don't stop the whole test

            time.sleep(2)  # brief pause between steps

    finally:
        try:
            child.sendcontrol("c")
            time.sleep(0.5)
            child.terminate(force=True)
        except Exception:
            pass

    # ── Report ──────────────────────────────────────────────────────
    total_elapsed = int(time.time() - start_time)
    print(f"\n{'='*60}")
    print(f"  RESULTS: {pkg}")
    print(f"  Total time: {total_elapsed}s")
    print(f"  Messages: {len(watcher.messages)}")
    print(f"  Writes: {watcher.count_writes()}")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    for r in results:
        status = "PASS" if r["met"] else "FAIL"
        if r["met"]:
            passed += 1
        else:
            failed += 1
        print(f"\n  Step {r['step']}: {status} ({r['elapsed']}s)")
        print(f"    {r['prompt']}")
        if r["errors"]:
            print(f"    Errors:")
            for e in r["errors"]:
                print(f"      {e[:100]}")

    print(f"\n  Steps: {passed}/{len(script)} passed")
    print(f"  TUI log: {log_path}")
    if watcher.session_dir:
        print(f"  Session: {watcher.session_dir}/messages.jsonl")

    # Save full results
    results_path = Path(f"/tmp/shakedown_interactive_{pkg}_results.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results: {results_path}")

    # ── TUI'S FUNCTIONAL TEST RESULT ─────────────────────────────────
    # The TUI ran functional_tests.sh as part of its session (via the
    # injected iteration steps). We parse what the TUI OBSERVED. The
    # harness does NOT run the test itself — the whole point is to test
    # whether the TUI can run, observe, and fix failures on its own.
    ft_status, ft_details = watcher.tui_functional_result()
    print(f"\n  ─────────── TUI FUNCTIONAL TEST RESULT ───────────")
    for line in ft_details.split("\n")[-20:]:
        print(f"  {line}")
    print(f"  ───────────────────────────────────────────────────")
    print(f"\n  TUI saw functional tests: {ft_status}")
    ft_pass = (ft_status == "PASS")
    print(f"{'='*60}\n")

    # Save to results JSON
    try:
        with open(results_path, "r") as f:
            results_data = json.load(f)
        final_result = {
            "tui_functional_status": ft_status,
            "tui_functional_details": ft_details,
            "total_elapsed_seconds": total_elapsed,
        }
        if isinstance(results_data, list):
            final_result["steps"] = results_data
        with open(results_path, "w") as f:
            json.dump(final_result, f, indent=2)
    except Exception:
        pass

    return 0 if ft_pass else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--pkg", required=True)
    args = parser.parse_args()
    cwd = Path(args.cwd).resolve()
    return run_interactive(cwd, args.pkg)


if __name__ == "__main__":
    sys.exit(main())
