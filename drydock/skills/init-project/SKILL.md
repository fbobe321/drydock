---
name: init-project
description: Initialize a new project with standard structure, README, tests, CI config.
allowed-tools: bash write_file read_file
user-invocable: true
disable-model-invocation: true
---

# Project Initialization

Create a new project with standard Python structure.

1. Create directory structure:
   ```
   $0/
   ├── $0/
   │   ├── __init__.py
   │   ├── __main__.py
   │   └── cli.py
   ├── tests/
   │   ├── __init__.py
   │   └── test_$0.py
   ├── pyproject.toml
   ├── README.md
   └── .gitignore
   ```
2. Write pyproject.toml with project metadata
3. Write a basic README.md
4. Write __init__.py with version
5. Write __main__.py entry point
6. Write a sample test
7. Initialize git: `git init && git add -A && git commit -m "Initial commit"`

If $ARGUMENTS specifies a framework (flask, fastapi, django), adapt the structure.
