---
name: migrate
description: Help with code migrations. Python version upgrades, framework migrations, dependency updates.
allowed-tools: bash read_file grep glob search_replace write_file
user-invocable: true
---

# Migration Helper

Assist with migrating code ($ARGUMENTS describes the migration).

## Common Migrations
- **Python 2→3**: Fix print statements, dict methods, string handling
- **Django version**: Update deprecated APIs, new settings
- **Flask→FastAPI**: Convert routes, add async, update schemas
- **unittest→pytest**: Convert test classes, assertions, fixtures
- **requirements.txt→pyproject.toml**: Convert dependency format
- **JavaScript→TypeScript**: Add types, rename files

## Workflow
1. Identify what needs to change (grep for deprecated patterns)
2. Plan the migration order (dependencies first)
3. Apply changes file by file
4. Run tests after each file
5. Fix any breakage
