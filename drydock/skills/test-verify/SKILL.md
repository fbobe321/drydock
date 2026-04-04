---
name: test-verify
description: Run tests, parse failures, fix failing tests. Structured test-driven debugging.
allowed-tools: bash read_file grep search_replace
user-invocable: true
---

# Test Verification Workflow

1. Discover test framework: look for pytest.ini, setup.cfg, tox.ini, or pyproject.toml
2. Run tests: `python -m pytest $ARGUMENTS -x -q --tb=short 2>&1 | head -50`
3. Parse the output:
   - Count: passed, failed, errors, skipped
   - For each failure: extract test name, file, line, assertion message
4. For each failing test:
   a. Read the test code to understand what it expects
   b. Read the source code it tests
   c. Identify the mismatch
   d. Fix the source (not the test) with search_replace
5. Re-run the failing test to verify: `python -m pytest <test_file>::<test_name> -x -q`
6. Report results

## Rules
- Fix source code, not tests (unless the test itself is wrong)
- Run only the specific failing test when verifying, not the full suite
- If a test fails due to environment issues (missing deps), report it instead of fixing
- Maximum 3 iterations per failing test
