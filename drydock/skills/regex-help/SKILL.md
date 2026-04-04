---
name: regex-help
description: Build, explain, and test regular expressions. Supports Python re module syntax.
allowed-tools: bash
user-invocable: true
---

# Regex Helper

Help with regular expressions based on $ARGUMENTS.

## Modes
- **Build**: "match email addresses" → provide the regex
- **Explain**: Given a regex, explain what it matches
- **Test**: Test a regex against sample strings
- **Debug**: Fix a regex that's not matching correctly

## Testing
```python
python3 -c "
import re
pattern = r'YOUR_REGEX'
tests = ['test1', 'test2', 'test3']
for t in tests:
    m = re.search(pattern, t)
    print(f'{t!r}: {\"MATCH\" if m else \"no match\"}  {m.groups() if m else \"\"}'  )
"
```

Always provide the regex in Python `re` module syntax with raw strings (r'...').
