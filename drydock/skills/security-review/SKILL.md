---
name: security-review
description: Security audit of code changes. Checks OWASP Top 10, secrets, injection, auth issues.
allowed-tools: bash read_file grep glob
user-invocable: true
---

# Security Review

Analyze the current changes (or specified files) for security vulnerabilities.

## Checks
1. **Secrets/Credentials**: grep for API keys, passwords, tokens, private keys
   - `grep -rn "password\|secret\|api_key\|token\|private_key\|AWS_" --include="*.py" | head -20`
2. **SQL Injection**: Look for string formatting in SQL queries
   - `grep -rn "f\".*SELECT\|\.format.*SELECT\|%.*SELECT" --include="*.py"`
3. **Command Injection**: Look for shell=True, os.system, subprocess with user input
   - `grep -rn "shell=True\|os\.system\|subprocess.*shell" --include="*.py"`
4. **XSS**: Look for unescaped user input in HTML/templates
5. **Path Traversal**: Look for user-controlled file paths without sanitization
6. **Auth/AuthZ**: Check for missing authentication or authorization checks
7. **Sensitive Data**: Check logging for PII, credentials in error messages
8. **Dependencies**: Check for known vulnerable packages

## Output Format
### CRITICAL (immediate fix required)
### HIGH (fix before merge)
### MEDIUM (fix soon)
### LOW (informational)

Each finding: [SEVERITY] file:line — description + recommended fix
