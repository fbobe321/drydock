Find files matching a glob pattern. Returns sorted file paths.

Examples:
- `glob(pattern="**/*.py")` — all Python files
- `glob(pattern="src/**/*.ts", path=".")` — TypeScript files under src/
- `glob(pattern="*.md")` — markdown files in current directory
- `glob(pattern="**/test_*.py")` — all test files

Use this instead of `bash find` for file discovery.
