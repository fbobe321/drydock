---
name: explore-code
description: Structured codebase exploration. Map architecture, find entry points, understand dependencies.
allowed-tools: bash read_file grep glob
context: fork
agent: explore
user-invocable: true
---

# Codebase Exploration

1. **Structure**: List top-level directories and key files
   - `find . -maxdepth 2 -type f -name "*.py" | head -30`
   - Look for: README, setup.py/pyproject.toml, main entry points

2. **Entry points**: Find how the code starts
   - Look for __main__.py, cli.py, app.py, manage.py
   - Read the main entry point (first 50 lines)

3. **Architecture**: Map the module structure
   - List packages (directories with __init__.py)
   - Identify the core modules vs utilities vs tests

4. **Dependencies**: Check what's imported
   - `grep -r "^import\|^from" --include="*.py" | sort -u | head -30`
   - Check requirements.txt or pyproject.toml dependencies

5. **Tests**: Find the test structure
   - `find . -name "test_*.py" -o -name "*_test.py" | head -20`

6. **Report**: Summarize findings in this format:
   ```
   PROJECT: name
   LANGUAGE: Python X.Y
   STRUCTURE: package/module layout
   ENTRY POINTS: main files
   KEY MODULES: core functionality
   TEST FRAMEWORK: pytest/unittest/etc
   DEPENDENCIES: key external deps
   ```

If $ARGUMENTS is provided, focus exploration on that specific area.
