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

from drydock.core.config import ProviderConfig, DrydockConfig
from drydock.core.llm.types import BackendLike
from drydock.core.types import LLMMessage, MessageList, Role


def _get_active_provider(config: DrydockConfig) -> ProviderConfig | None:
    """Get the provider that serves the active model.

    The config may have multiple providers (e.g., Mistral cloud + local vLLM).
    We need the one that actually serves the active_model.
    """
    # Find which provider the active model uses
    active_model_config = None
    for m in config.models:
        if m.name == config.active_model or m.alias == config.active_model:
            active_model_config = m
            break

    if active_model_config and active_model_config.provider:
        for p in config.providers:
            if p.name == active_model_config.provider:
                return p

    # Fallback: prefer localhost providers
    for p in config.providers:
        if "localhost" in p.api_base or "127.0.0.1" in p.api_base:
            return p

    # Last resort: first provider
    return config.providers[0] if config.providers else None

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


_COMMON_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "with", "from", "for", "and",
    "but", "or", "not", "no", "so", "if", "then", "else", "when", "where",
    "how", "what", "which", "who", "that", "this", "these", "those", "it",
    "its", "to", "of", "in", "on", "at", "by", "as", "up", "out", "off",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "only", "own", "same", "than", "too", "very", "just", "about",
    "python", "usr", "bin", "home", "data", "tmp", "pip", "test", "tests",
    "import", "class", "def", "return", "self", "none", "true", "false",
    "file", "files", "using", "based", "build", "create", "run", "use",
}


def make_plan_from_prd(user_prompt: str) -> BuildPlan:
    """Extract a build plan from the PRD text. No LLM needed."""
    import re
    text = user_prompt.lower()

    # 1. Extract package name
    pkg_name = None

    # Try: explicit package directory in structure section (/pkg_name\n  ├──)
    dir_match = re.search(r'/(\w+)\s*\n\s*[├└ ]', user_prompt)
    if dir_match and dir_match.group(1).lower() not in _COMMON_WORDS:
        pkg_name = dir_match.group(1)

    # Try: "package_name/ package" pattern
    if not pkg_name:
        m = re.search(r'(\w+)/\s*package\b', user_prompt, re.IGNORECASE)
        if m and m.group(1).lower() not in _COMMON_WORDS:
            pkg_name = m.group(1)

    # Try: explicit "package_name/" with __init__.py or __main__.py
    if not pkg_name:
        m = re.search(r'(\w+)/(?:__init__|__main__|cli)\.py', user_prompt)
        if m and m.group(1).lower() not in _COMMON_WORDS:
            pkg_name = m.group(1)

    # Try: "python3 -m package_name" in usage
    if not pkg_name:
        m = re.search(r'python3?\s+-m\s+(\w+)', user_prompt)
        if m and m.group(1).lower() not in _COMMON_WORDS:
            pkg_name = m.group(1)

    # Try: title-derived name (first heading)
    if not pkg_name:
        title_match = re.search(r'#\s+(.+)', user_prompt)
        if title_match:
            title = title_match.group(1).strip()
            # Remove emoji, special chars, "PRD" prefix
            title = re.sub(r'[^\w\s]', '', title).strip()
            title = re.sub(r'\bPRD\w*\b', '', title, flags=re.IGNORECASE).strip()
            words = title.lower().split()
            # Filter common words, take first 2 significant words
            sig_words = [w for w in words if w not in _COMMON_WORDS and len(w) > 2][:2]
            if sig_words:
                pkg_name = "_".join(sig_words)

    if not pkg_name:
        pkg_name = "project"

    # Sanitize: lowercase, underscores, no leading digits
    pkg_name = re.sub(r'[^a-z0-9_]', '_', pkg_name.lower())
    pkg_name = re.sub(r'_+', '_', pkg_name).strip('_')
    if pkg_name[0].isdigit():
        pkg_name = "pkg_" + pkg_name

    # 2. Extract module files from PRD
    py_files = re.findall(r'(\w+)\.py', user_prompt)
    py_files = [f for f in py_files if f.lower() not in ("__init__", "__main__", "test", "setup", "conftest", "config")]
    py_files = list(dict.fromkeys(py_files))  # deduplicate

    # 3. If not enough files found, infer from PRD structure
    if len(py_files) < 3:
        inferred = list(py_files)
        if "cli" not in inferred:
            inferred.append("cli")

        # Only add modules that are CORE to the project type
        # (not every keyword match — that gives false positives)
        keyword_modules = [
            (("store", "storage", "persist", "save", "load", "json file"), "store"),
            (("parse", "parser", "ingest"), "parser"),
            (("pattern", "detect pattern"), "patterns"),
            (("anomal", "spike detection"), "anomaly"),
            (("root cause", "suggest"), "root_cause"),
            (("convert", "converter"), "converter"),
            (("organiz", "organizer"), "organizer"),
            (("generat", "generator"), "generator"),
        ]
        for keywords, module in keyword_modules:
            # Only match if module name appears explicitly OR keyword is prominent
            if module in text or any(text.count(kw) >= 2 for kw in keywords):
                if module not in inferred:
                    inferred.append(module)

        # Add models.py if the project has data structures
        if any(kw in text for kw in ("class ", "dataclass", "model")):
            if "models" not in inferred:
                inferred.append("models")

        # Always add utils
        if "utils" not in inferred:
            inferred.append("utils")
        py_files = inferred

    # Ensure cli is last
    if "cli" in py_files:
        py_files.remove("cli")
    py_files.append("cli")

    # 4. Build file specs
    files = []
    non_cli = [f for f in py_files if f != "cli"]
    for f in py_files:
        imports = [f"{pkg_name}.{m}" for m in non_cli] if f == "cli" else []
        # Extract purpose from PRD
        purpose = f"Core {f} module"
        for line in user_prompt.split("\n"):
            line_lower = line.lower()
            if f in line_lower and len(line.strip()) > 15 and not line.strip().startswith("#"):
                purpose = line.strip()[:120]
                break
        files.append(FileSpec(f"{pkg_name}/{f}.py", purpose, imports))

    # 5. Description from title
    desc = pkg_name.replace("_", " ").title()
    title_match = re.search(r'#\s+(.+)', user_prompt)
    if title_match:
        desc = re.sub(r'[^\w\s]', '', title_match.group(1)).strip()[:80]

    entry = "cli"
    logger.info("Plan from PRD: package=%s, files=%d, entry=%s", pkg_name, len(files), entry)
    return BuildPlan(package_name=pkg_name, description=desc, entry_point=entry, files=files)


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

{existing_apis}

RULES:
- Use write_file to write the complete file
- Use ABSOLUTE imports: `from {package_name}.module import X`
- Do NOT use relative imports (from .module)
- Write production-quality code with type hints and docstrings
- If this is the CLI module, it MUST have a `def main():` function using argparse
- After writing the file, STOP immediately
"""


def _extract_public_api(file_path: Path) -> str:
    """Extract public function/class names from an implemented file."""
    import ast
    try:
        content = file_path.read_text()
        if "# TODO: implement" in content:
            return ""
        tree = ast.parse(content)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    args = ", ".join(a.arg for a in node.args.args)
                    names.append(f"  def {node.name}({args})")
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    names.append(f"  class {node.name}")
        return "\n".join(names) if names else ""
    except Exception:
        return ""


async def implement_file(
    file_spec: FileSpec,
    plan: BuildPlan,
    config: DrydockConfig,
    base_dir: Path,
    backend: BackendLike | None = None,
) -> bool:
    """Ask the LLM to generate code for one file."""
    module_lines = []
    for f in plan.files:
        marker = " <-- THIS FILE" if f.path == file_spec.path else ""
        module_lines.append(f"  {f.path}: {f.purpose}{marker}")
    module_listing = "\n".join(module_lines)

    import_note = ""
    if file_spec.imports:
        import_note = f"This file should import from: {', '.join(file_spec.imports)}"

    # Pass already-implemented APIs so this file uses matching names
    existing_apis = ""
    api_parts = []
    for f in plan.files:
        if f.path == file_spec.path:
            continue
        fp = base_dir / f.path
        api = _extract_public_api(fp)
        if api:
            api_parts.append(f"Already implemented in {f.path}:\n{api}")
    if api_parts:
        existing_apis = "EXISTING APIs (use these EXACT names when importing):\n" + "\n\n".join(api_parts)

    prompt = FILE_PROMPT.format(
        file_path=file_spec.path,
        package_name=plan.package_name,
        purpose=file_spec.purpose,
        module_listing=module_listing,
        import_note=import_note,
        existing_apis=existing_apis,
    )

    try:
        system_msg = (
            "You are a code implementation agent. Write a CONCISE Python file. "
            "Output ONLY Python code. No markdown fences. No explanation. "
            "FIRST LINE MUST BE: from __future__ import annotations\n"
            "Keep it under 150 lines. Use absolute imports (from pkg.module import X). "
            "Do NOT use relative imports. Do NOT import classes just for type hints — "
            "use string literals instead: def foo(x: 'ClassName'). "
            "Import EXACT names from the EXISTING APIs listed."
        )

        msgs = MessageList()
        msgs.append(LLMMessage(role=Role.system, content=system_msg))
        msgs.append(LLMMessage(role=Role.user, content=prompt))

        if backend:
            # Use the same backend as the main agent loop
            active_model = config.get_active_model()
            result = await backend.complete(
                model=active_model,
                messages=msgs,
                temperature=0.2,
                max_tokens=2048,
            )
            content = result.message.content or ""
        else:
            # Fallback: direct httpx call
            import httpx
            provider = _get_active_provider(config)
            if not provider:
                return False
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{provider.api_base}/chat/completions",
                    json={
                        "model": config.active_model,
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 2048,
                        "temperature": 0.2,
                    },
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]

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

        # Ensure from __future__ import annotations is first line
        if "from __future__ import annotations" not in content:
            content = "from __future__ import annotations\n\n" + content
            file_path.write_text(content)

        return True

    except Exception as e:
        logger.warning("Failed to implement %s: %s", file_spec.path, e)
        return False


# ============================================================================
# Main orchestrator
# ============================================================================

async def run_build_pipeline(
    user_prompt: str,
    config: DrydockConfig,
    base_dir: Path | None = None,
    backend: BackendLike | None = None,
) -> str:
    """Run the full multi-phase build pipeline.

    Returns a summary string for the main agent to display.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    # Phase 1: Plan (deterministic — no LLM needed)
    logger.info("BUILD PHASE 1: Planning...")
    plan = make_plan_from_prd(user_prompt)

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
        for attempt in range(3):
            try:
                success = await implement_file(file_spec, plan, config, base_dir, backend)
                if success:
                    _fix_imports(base_dir / file_spec.path, plan.package_name)
                    break
                else:
                    logger.warning("  Attempt %d failed for %s, retrying...", attempt + 1, file_spec.path)
            except Exception as e:
                logger.warning("  Attempt %d error for %s: %s", attempt + 1, file_spec.path, e)
        results.append((file_spec.path, success))

    # Phase 3.5: Auto-fix imports
    logger.info("BUILD PHASE 3.5: Fixing imports...")
    _fix_cross_file_imports(plan, base_dir)
    _fix_circular_imports(plan, base_dir)

    # Phase 3.75: Verify the package actually imports — fix errors iteratively
    logger.info("BUILD PHASE 3.75: Verify & fix...")
    _verify_and_fix_imports(plan, base_dir)

    # Build summary
    succeeded = sum(1 for _, ok in results if ok)
    failed_files = [path for path, ok in results if not ok]

    # List the actual public APIs so the model can fix imports
    api_summary_parts = []
    for f in plan.files:
        fp = base_dir / f.path
        api = _extract_public_api(fp) if fp.exists() else ""
        status = "DONE" if any(p == f.path and ok for p, ok in results) else "STUB"
        api_summary_parts.append(f"  {f.path} [{status}]")
        if api:
            for line in api.split("\n"):
                api_summary_parts.append(f"    {line.strip()}")

    summary_lines = [
        f"I have already created all files for package '{plan.package_name}'.",
        f"",
        f"Files and their APIs:",
    ]
    summary_lines.extend(api_summary_parts)
    summary_lines.extend([
        f"",
        f"YOUR NEXT STEPS (follow exactly):",
        f"1. Run: python3 -m {plan.package_name} --help",
        f"2. If ImportError or NameError: read the failing file, fix with search_replace",
        f"3. Create a sample log file with write_file, then test: python3 -m {plan.package_name} --log-file sample.log",
        f"4. When it works, tell the user",
        f"",
        f"DO NOT run git commands. DO NOT run ls. DO NOT create new .py files.",
    ])

    if failed_files:
        summary_lines.extend([
            f"",
            f"These files need implementation: {', '.join(failed_files)}",
            f"Use write_file with overwrite=true to implement them.",
        ])

    return "\n".join(summary_lines)


def _fix_cross_file_imports(plan: BuildPlan, base_dir: Path) -> None:
    """Fix import name mismatches between files.

    Each file was built by a separate LLM call, so they may reference
    function/class names that don't exist in the target module.
    This function scans all imports and renames them to match actual definitions.
    """
    import ast

    # Step 1: Build a map of what each module actually exports
    exports: dict[str, set[str]] = {}  # module_path -> {name1, name2, ...}
    for f in plan.files:
        fp = base_dir / f.path
        if not fp.exists():
            continue
        try:
            content = fp.read_text()
            tree = ast.parse(content)
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(node.name)
                elif isinstance(node, ast.ClassDef):
                    names.add(node.name)
            module_name = f"{plan.package_name}.{fp.stem}"
            exports[module_name] = names
        except Exception:
            continue

    # Step 2: For each file, check if its imports reference names that don't exist
    for f in plan.files:
        fp = base_dir / f.path
        if not fp.exists():
            continue
        try:
            content = fp.read_text()
            tree = ast.parse(content)
            replacements: list[tuple[str, str]] = []

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                    if module not in exports:
                        continue
                    available = exports[module]
                    for alias in node.names:
                        if alias.name not in available and alias.name != "*":
                            # Find the closest match
                            best = _find_closest(alias.name, available)
                            if best:
                                replacements.append((alias.name, best))

            if replacements:
                for old_name, new_name in replacements:
                    content = content.replace(old_name, new_name)
                fp.write_text(content)
                logger.info("Fixed imports in %s: %s",
                            fp.name, ", ".join(f"{o}->{n}" for o, n in replacements))
        except Exception as e:
            logger.debug("Failed to fix imports in %s: %s", f.path, e)


def _find_closest(name: str, available: set[str]) -> str | None:
    """Find the closest matching name from available names."""
    name_lower = name.lower()

    # Exact match (case-insensitive)
    for a in available:
        if a.lower() == name_lower:
            return a

    # Substring match (the wanted name is part of an available name or vice versa)
    for a in available:
        if name_lower in a.lower() or a.lower() in name_lower:
            return a

    # Word overlap (split by _ and CamelCase)
    import re
    name_words = set(re.findall(r'[a-z]+', name_lower))
    best_match = None
    best_overlap = 0
    for a in available:
        a_words = set(re.findall(r'[a-z]+', a.lower()))
        overlap = len(name_words & a_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = a
    if best_overlap >= 1:
        return best_match

    return None


def _fix_circular_imports(plan: BuildPlan, base_dir: Path) -> None:
    """Detect and break circular imports by removing unnecessary ones."""
    import ast

    # Build import graph: module -> set of modules it imports from
    graph: dict[str, set[str]] = {}
    for f in plan.files:
        fp = base_dir / f.path
        if not fp.exists():
            continue
        module = f"{plan.package_name}.{fp.stem}"
        graph[module] = set()
        try:
            tree = ast.parse(fp.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith(plan.package_name + "."):
                        graph[module].add(node.module)
        except Exception:
            continue

    # Find cycles
    for mod_a in graph:
        for mod_b in graph.get(mod_a, set()):
            if mod_a in graph.get(mod_b, set()):
                # Circular: A imports B and B imports A
                # Break by removing the import from the "utility" module
                # (the one with fewer definitions is likely the utility)
                file_a = base_dir / plan.package_name / f"{mod_a.split('.')[-1]}.py"
                file_b = base_dir / plan.package_name / f"{mod_b.split('.')[-1]}.py"
                try:
                    content_b = file_b.read_text()
                    # Remove the import line from B that imports A
                    lines = content_b.split("\n")
                    new_lines = []
                    removed = False
                    for line in lines:
                        if f"from {mod_a} import" in line and not removed:
                            logger.info("Breaking circular import: removed '%s' from %s",
                                        line.strip(), file_b.name)
                            removed = True
                            continue
                        new_lines.append(line)
                    if removed:
                        file_b.write_text("\n".join(new_lines))
                except Exception:
                    pass


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
