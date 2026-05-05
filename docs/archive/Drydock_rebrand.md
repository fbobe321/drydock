# Drydock CLI Rebranding Design Document

## 1. Overview

This document outlines the rebranding of the existing `mistral-vibe` CLI application into a new product called **Drydock**.

The goal is to:

* Fully remove **Mistral branding** from the user interface (TUI, CLI, banners)
* Introduce a **nautical-themed identity**
* Integrate **one-word state terms** from `drydock_terms.md`
* Remain fully compliant with the **Apache License 2.0**

---

## 2. Goals

### Primary Goals

* Replace all visible branding with **Drydock**
* Implement **nautical UI language and tone**
* Add dynamic **state/status system** using `drydock_terms.md`
* Preserve all existing functionality

### Secondary Goals

* Improve perceived intelligence via **state progression UX**
* Prepare foundation for future features (agent loops, crew system)

---

## 3. Non-Goals

* No changes to core model inference logic
* No changes to API providers (vLLM, OpenAI-compatible, etc.)
* No major refactor of architecture (unless required for branding hooks)

---

## 4. Licensing Compliance (CRITICAL)

Drydock must comply with **Apache License 2.0**.

### Required Actions

#### 4.1 Preserve License

* Keep original `LICENSE` file unchanged
* Include Apache 2.0 license in distribution

#### 4.2 Add NOTICE file

Create or update `NOTICE` file:

```
This product includes software derived from mistralai/mistral-vibe.

Original work:
Copyright 2025 Mistral AI

Licensed under the Apache License, Version 2.0

Modifications:
- Rebranding to Drydock
- UI/UX changes (TUI, CLI output, banners)
- Added nautical state system
```

#### 4.3 Source Attribution

* Keep existing copyright headers
* Add modification notice where files are changed

---

## 5. Branding Changes

### 5.1 Name Replacement

Globally replace:

| Old          | New         |
| ------------ | ----------- |
| mistral-vibe | drydock     |
| Vibe         | Drydock     |
| vibe CLI     | drydock CLI |
| vibe command | drydock     |

Search targets:

* CLI entrypoints
* Package names (optional, careful with imports)
* Help text
* README
* Config defaults

---

### 5.2 CLI Command

Rename executable:

```
vibe → drydock
```

Ensure:

* `setup.py` / `pyproject.toml` updated
* Entry point reflects new command

---

### 5.3 Startup Banner (TUI)

Replace existing banner with:

```
⚓ Drydock — Command Deck Interface
```

Optional extended version:

```
⚓ Drydock
Command Deck Interface
"Chart your course. Execute with precision."
```

Remove:

* All "Mistral" references
* All "Vibe" references

---

## 6. TUI (Terminal UI) Updates

### 6.1 Status Line System

Replace generic states like:

* "Thinking..."
* "Planning..."
* "Executing..."

With dynamic one-word nautical terms from:

```
drydock_terms.md
```

---

### 6.2 State Engine

Implement a lightweight state mapping:

```python
STATE_CATEGORIES = {
    "plan": [...],
    "search": [...],
    "reason": [...],
    "execute": [...],
    "debug": [...],
    "retry": [...],
    "error": [...],
    "complete": [...]
}
```

Each category pulls from `drydock_terms.md`.

---

### 6.3 State Selection Logic

* Random selection within category
* Avoid repeating last 2 states
* Optional: weighted rotation

Example:

```
Charting → Sounding → Adjusting → Executing → Docking
```

---

### 6.4 Output Format

Replace:

```
Thinking...
```

With:

```
⚓ Charting...
```

Rules:

* Always prefix with ⚓
* One-word only
* Ends with ellipsis (...)

---

## 7. Tone & Language

All CLI output should follow:

| Style      | Example            |
| ---------- | ------------------ |
| Nautical   | "Charting course"  |
| Mechanical | "Executing"        |
| Clean      | No emojis beyond ⚓ |

Avoid:

* Casual phrases
* Modern slang
* AI jargon in user-facing text

---

## 8. File Changes

### 8.1 Required Updates

* `README.md`
* CLI entrypoint file
* TUI rendering module
* Status/progress module
* Help text / CLI descriptions

### 8.2 New File

```
drydock_terms.md
```

Used as:

* Source of truth for all states

---

## 9. Config Updates (Optional)

Add config option:

```json
{
  "ui": {
    "theme": "drydock",
    "dynamic_states": true
  }
}
```

---

## 10. Testing Requirements

### Functional

* CLI runs with new name
* No broken imports
* TUI renders correctly

### Branding

* No "Mistral" visible in UI
* No "Vibe" visible in UI

### License

* LICENSE present
* NOTICE present

---

## 11. Future Enhancements (Not in Scope)

* Crew-based UI (Captain, Navigator, Engineer)
* Multi-agent orchestration layer
* Persistent state tracking
* Voyage logs (session memory)

---

## 12. Acceptance Criteria

* CLI command is `drydock`
* All visible branding replaced
* Nautical state system active
* Apache 2.0 compliance maintained
* No regression in functionality

---

## 13. Summary

Drydock is a **rebranded, enhanced CLI agent system** built on `mistral-vibe`, introducing:

* Nautical identity
* Intelligent state UX
* Clean CLI experience

While remaining:

* Fully legal under Apache 2.0
* Fully compatible with existing architecture

---

