---
name: doc-gen
description: Generate documentation for code. Docstrings, README, API docs, usage examples.
allowed-tools: read_file grep glob search_replace write_file
user-invocable: true
---

# Documentation Generator

Generate documentation for the specified code ($ARGUMENTS or current project).

## Modes
1. **Docstrings**: Add/update docstrings for functions, classes, methods
2. **README**: Generate or update README.md with usage, installation, API
3. **API docs**: Generate API reference documentation
4. **Examples**: Create usage examples

## Workflow
1. Read the source files to document
2. Understand the public API (exported functions, classes)
3. Generate documentation in the appropriate format
4. Write the documentation files

## Rules
- Follow Google-style or NumPy-style docstrings (match existing style)
- Include type hints in docstrings if not in code
- Document parameters, return values, exceptions
- Add usage examples for non-obvious functions
