"""Multi-phase build orchestrator.

Instead of running everything in one context window (where the model wastes
turns on packaging/import errors), this splits project builds into phases:

Phase 1: PLAN — planner subagent reads the PRD, outputs a JSON build plan
Phase 2: SCAFFOLD — pure Python creates dirs, __init__.py, __main__.py
Phase 3: IMPLEMENT — one subagent per file, each with clean context
Phase 4: (back in main loop) — test and fix

Each phase gets a separate context window, so the model only does what
it's good at (writing logic) and the framework handles what it's bad at
(packaging, imports, project structure).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from drydock.core.config import VibeConfig

if TYPE_CHECKING:
    from drydock.core.agents.manager import AgentManager
    from drydock.core.types import EntrypointMetadata

logger = logging.getLogger(__name__)


# ============================================================================
# Data structures
# ============================================================================

@dataclass
class FileSpec:
    path: str
    purpose: str
    imports: list[str] = field(default_factory=list)


@dataclass
class BuildPlan:
    package_name: str
    description: str
    entry_point: str
    files: list[FileSpec]


# ============================================================================
# Phase 1: PLAN
# ============================================================================

PLAN_PROMPT = """Analyze this project request and output a JSON build plan.

PROJECT REQUEST:
{user_prompt}

Output a JSON block inside ```json fences with this EXACT structure:
```json
{{
  "package_name": "my_package",
  "description": "One-line description of the project",
  "entry_point": "cli",
  "files": [
    {{"path": "my_package/utils.py", "purpose": "Utility functions for X and Y", "imports": []}},
    {{"path": "my_package/core.py", "purpose": "Core logic that does Z", "imports": ["my_package.utils"]}},
    {{"path": "my_package/cli.py", "purpose": "CLI entry point with main() function using argparse", "imports": ["my_package.core"]}}
  ]
}}
```

RULES:
- package_name: valid Python identifier, lowercase, underscores OK
- entry_point: the module with main() function (usually "cli")
- files: list in DEPENDENCY ORDER (dependencies first, entry_point last)
- Each purpose must be specific enough to implement the file from
- Do NOT include __init__.py or __main__.py (created automatically)
- The entry_point module MUST have a main() function
"""


def extract_plan(response_text: str) -> BuildPlan:
    """Extract a BuildPlan from the planner's free-text response."""
    # Try fenced JSON block first
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", response_text, re.DOTALL)
    if match:
        raw = match.group(1)
    else:
        # Fallback: find first { ... } with "package_name"
        match = re.search(r"\{[^{}]*\"package_name\".*\}", response_text, re.DOTALL)
        if match:
            raw = match.group(0)
        else:
            raise ValueError("No JSON build plan found in planner response")

    data = json.loads(raw)

    files = []
    for f in data.get("files", []):
        files.append(FileSpec(
            path=f["path"],
            purpose=f["purpose"],
            imports=f.get("imports", []),
        ))

    return BuildPlan(
        package_name=data["package_name"],
        description=data.get("description", ""),
        entry_point=data.get("entry_point", "cli"),
        files=files,
    )


async def run_planner(
    user_prompt: str,
    config: VibeConfig,
) -> BuildPlan:
    """Phase 1: Direct API call to create a build plan. No AgentLoop needed."""
    import httpx

    plan_prompt = PLAN_PROMPT.format(user_prompt=user_prompt)

    provider = config.providers[0] if config.providers else None
    if not provider:
        raise RuntimeError("No provider configured")

    messages = [
        {"role": "system", "content": "You are a project planner. Output ONLY a JSON build plan."},
        {"role": "user", "content": plan_prompt},
    ]

    # Truncate very long prompts to avoid timeout
    if len(plan_prompt) > 6000:
        plan_prompt = plan_prompt[:6000] + "\n\n[TRUNCATED — focus on the core features listed above]"

    messages[1]["content"] = plan_prompt

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{provider.api_base}/chat/completions",
            json={
                "model": config.active_model,
                "messages": messages,
                "max_tokens": 2048,
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    full_response = data["choices"][0]["message"]["content"]
    logger.info("Planner response length: %d chars", len(full_response))

    return extract_plan(full_response)


# ============================================================================
# Phase 2: SCAFFOLD
# ============================================================================

def scaffold_package(plan: BuildPlan, base_dir: Path) -> list[Path]:
    """Phase 2: Create package structure deterministically. No LLM needed."""
    pkg_dir = base_dir / plan.package_name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    created = []

    # __init__.py
    init_path = pkg_dir / "__init__.py"
    init_path.write_text(f'"""{plan.description}"""\n')
    created.append(init_path)

    # __main__.py with correct absolute import
    main_path = pkg_dir / "__main__.py"
    main_path.write_text(
        f"from {plan.package_name}.{plan.entry_point} import main\n\n"
        f'if __name__ == "__main__":\n'
        f"    main()\n"
    )
    created.append(main_path)

    # Create stub files so imports resolve during implementation
    for file_spec in plan.files:
        fp = base_dir / file_spec.path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(f'"""{file_spec.purpose}"""\n\n# TODO: implement\n')
        created.append(fp)

    return created


# ============================================================================
# Phase 3: IMPLEMENT (one subagent per file)
# ============================================================================

FILE_PROMPT = """Write the COMPLETE implementation for `{file_path}`.

Package: {package_name}
Purpose: {purpose}

Other modules in this package:
{module_listing}

{import_note}

RULES:
- Use write_file to write the complete file
- Use ABSOLUTE imports: `from {package_name}.module import X`
- Do NOT use relative imports (from .module)
- Write production-quality code with type hints and docstrings
- If this is the CLI module, it MUST have a `def main():` function using argparse
- After writing the file, STOP immediately
"""


async def implement_file(
    file_spec: FileSpec,
    plan: BuildPlan,
    config: VibeConfig,
    base_dir: Path,
) -> bool:
    """Ask the LLM to generate code for one file via direct API call.

    Instead of spawning a full AgentLoop (which causes recursion issues),
    we make a direct chat completion call and write the result to disk.
    This is much lighter — no tools, no loop, no context management.
    """
    module_lines = []
    for f in plan.files:
        marker = " <-- THIS FILE" if f.path == file_spec.path else ""
        module_lines.append(f"  {f.path}: {f.purpose}{marker}")
    module_listing = "\n".join(module_lines)

    import_note = ""
    if file_spec.imports:
        import_note = f"This file should import from: {', '.join(file_spec.imports)}"

    prompt = FILE_PROMPT.format(
        file_path=file_spec.path,
        package_name=plan.package_name,
        purpose=file_spec.purpose,
        module_listing=module_listing,
        import_note=import_note,
    )

    # Direct API call — no AgentLoop overhead
    try:
        import httpx

        # Find the provider config
        provider = config.providers[0] if config.providers else None
        if not provider:
            return False

        messages = [
            {"role": "system", "content": (
                "You are a code implementation agent. Write the COMPLETE Python file. "
                "Output ONLY the Python code, no markdown fences, no explanation. "
                "Use absolute imports (from package.module import X, NOT from .module import X)."
            )},
            {"role": "user", "content": prompt},
        ]

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{provider.api_base}/chat/completions",
                json={
                    "model": config.active_model,
                    "messages": messages,
                    "max_tokens": 4096,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]

        # Strip markdown fences if present
        if "```python" in content:
            content = content.split("```python", 1)[1]
            if "```" in content:
                content = content.split("```", 1)[0]
        elif "```" in content:
            parts = content.split("```")
            if len(parts) >= 3:
                content = parts[1]
                if content.startswith("python\n"):
                    content = content[7:]

        content = content.strip() + "\n"

        # Write the file
        file_path = base_dir / file_spec.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

        # Verify syntax
        import ast
        try:
            ast.parse(content)
        except SyntaxError as e:
            logger.warning("Syntax error in generated %s: %s", file_spec.path, e)
            return False

        return True

    except Exception as e:
        logger.warning("Failed to implement %s: %s", file_spec.path, e)
        return False


# ============================================================================
# Main orchestrator
# ============================================================================

async def run_build_pipeline(
    user_prompt: str,
    config: VibeConfig,
    base_dir: Path | None = None,
) -> str:
    """Run the full multi-phase build pipeline.

    Returns a summary string for the main agent to display.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    # Phase 1: Plan
    logger.info("BUILD PHASE 1: Planning...")
    try:
        plan = await run_planner(user_prompt, config)
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"Planning failed: {e}") from e

    logger.info("Plan: package=%s, files=%d, entry=%s",
                plan.package_name, len(plan.files), plan.entry_point)

    # Phase 2: Scaffold
    logger.info("BUILD PHASE 2: Scaffolding...")
    created = scaffold_package(plan, base_dir)
    logger.info("Scaffolded %d files", len(created))

    # Phase 3: Implement each file
    logger.info("BUILD PHASE 3: Implementing %d files...", len(plan.files))
    results = []
    for i, file_spec in enumerate(plan.files):
        logger.info("  Implementing [%d/%d]: %s", i + 1, len(plan.files), file_spec.path)
        # Try up to 2 times per file
        success = False
        for attempt in range(2):
            try:
                success = await implement_file(file_spec, plan, config, base_dir)
                if success:
                    _fix_imports(base_dir / file_spec.path, plan.package_name)
                    break
                else:
                    logger.warning("  Attempt %d failed for %s, retrying...", attempt + 1, file_spec.path)
            except Exception as e:
                logger.warning("  Attempt %d error for %s: %s", attempt + 1, file_spec.path, e)
        results.append((file_spec.path, success))

    # Build summary
    succeeded = sum(1 for _, ok in results if ok)
    failed_files = [path for path, ok in results if not ok]

    summary_lines = [
        f"PROJECT BUILT: {plan.package_name}",
        f"Description: {plan.description}",
        f"Files: {succeeded}/{len(plan.files)} implemented",
        f"",
        f"Package structure:",
    ]
    for f in plan.files:
        status = "OK" if any(p == f.path and ok for p, ok in results) else "STUB"
        summary_lines.append(f"  {f.path} [{status}] — {f.purpose}")

    summary_lines.extend([
        f"",
        f"Entry point: python3 -m {plan.package_name}",
        f"",
        f"Test the project now by running: python3 -m {plan.package_name} --help",
        f"If it fails, read the error and fix with search_replace.",
    ])

    if failed_files:
        summary_lines.extend([
            f"",
            f"NEEDS IMPLEMENTATION: {', '.join(failed_files)}",
            f"These files have stubs only. Implement them with write_file.",
        ])

    return "\n".join(summary_lines)


def _fix_imports(file_path: Path, package_name: str) -> None:
    """Fix relative imports in a file to absolute imports."""
    try:
        if not file_path.exists():
            return
        content = file_path.read_text()
        if "from ." not in content:
            return
        fixed = re.sub(
            r"from \.([\w.]+) import",
            f"from {package_name}.\\1 import",
            content,
        )
        fixed = re.sub(
            r"from \. import",
            f"from {package_name} import",
            fixed,
        )
        if fixed != content:
            file_path.write_text(fixed)
            logger.info("Fixed relative imports in %s", file_path.name)
    except Exception:
        pass
