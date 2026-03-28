Invoke a skill by name to activate specialized workflows.

Examples:
- `invoke_skill(skill_name="investigate", arguments="Fix the login timeout bug")`
- `invoke_skill(skill_name="review")` — run a code review
- `invoke_skill(skill_name="ship")` — run the shipping pipeline
- `invoke_skill(skill_name="batch", arguments="Add type hints to all files in src/")`

Available skills depend on what's installed. Use this to activate domain-specific workflows.
