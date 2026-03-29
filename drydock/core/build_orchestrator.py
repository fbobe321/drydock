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

from drydock.core.agent_loop import AgentLoop
from drydock.core.agents.models import BuiltinAgentName
from drydock.core.config import Backend, ModelConfig, ProviderConfig, SessionLoggingConfig, VibeConfig
from drydock.core.types import AssistantEvent, ToolResultEvent

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
    max_turns: int = 8,
) -> BuildPlan:
    """Phase 1: Spawn a planner subagent to create a build plan."""
    plan_prompt = PLAN_PROMPT.format(user_prompt=user_prompt)

    planner = AgentLoop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        max_turns=max_turns,
    )

    response_parts = []
    async for event in planner.act(plan_prompt):
        if isinstance(event, AssistantEvent) and event.content:
            response_parts.append(event.content)

    full_response = "\n".join(response_parts)
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
) -> bool:
    """Spawn a builder subagent to implement one file."""
    # Build module listing
    module_lines = []
    for f in plan.files:
        marker = " ← THIS FILE" if f.path == file_spec.path else ""
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

    builder = AgentLoop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        max_turns=6,
    )

    wrote_file = False
    async for event in builder.act(prompt):
        if isinstance(event, ToolResultEvent) and event.tool_name == "write_file":
            if not event.error:
                wrote_file = True

    return wrote_file


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
        try:
            success = await implement_file(file_spec, plan, config)
            results.append((file_spec.path, success))
            if success:
                # Post-fix: ensure absolute imports
                _fix_imports(base_dir / file_spec.path, plan.package_name)
        except Exception as e:
            logger.warning("  Failed to implement %s: %s", file_spec.path, e)
            results.append((file_spec.path, False))

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
