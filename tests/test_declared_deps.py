"""Regression guard for the v3.1.15 httpx bug: a module imported directly by the
package MUST be declared in pyproject dependencies. Relying on transitive deps
(httpx via openai, rich via textual) breaks a clean/partial `pip install` at
runtime with ModuleNotFoundError; pypdf isn't transitive at all.

This scans drydock/ with ast, drops stdlib + first-party, and asserts every
remaining top-level import is a declared dependency. It would have failed on
3.1.14 (httpx, pypdf, rich undeclared)."""
import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "drydock"

# import-name -> distribution-name, only where they differ (none currently, but
# future-proofs the check for e.g. PIL->pillow, yaml->pyyaml).
IMPORT_TO_DIST = {"PIL": "pillow", "yaml": "pyyaml", "bs4": "beautifulsoup4"}


def _declared_dists() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    out = set()
    # core deps AND every optional-dependency extra count as "declared": a package
    # imported LAZILY and declared in an extra (e.g. [pdf-redbox]) doesn't break a
    # clean base install — the feature is just unavailable until you install it.
    specs = list(data["project"].get("dependencies", []))
    for extra in data["project"].get("optional-dependencies", {}).values():
        specs.extend(extra)
    for spec in specs:
        # strip version/extras/markers → bare distribution name, normalized
        name = spec.split(";")[0].split("[")[0]
        for sep in ("==", ">=", "<=", "~=", ">", "<", "!=", " "):
            name = name.split(sep)[0]
        out.add(name.strip().lower().replace("_", "-"))
    return out


def _top_level_thirdparty_imports() -> set[str]:
    stdlib = set(sys.stdlib_module_names)
    mods: set[str] = set()
    for py in PKG.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:  # skip relative imports
                    mods.add(node.module.split(".")[0])
    return {m for m in mods if m and m not in stdlib and m != "drydock"}


def test_all_direct_imports_are_declared_dependencies():
    declared = _declared_dists()
    missing = []
    for imp in sorted(_top_level_thirdparty_imports()):
        dist = IMPORT_TO_DIST.get(imp, imp).lower().replace("_", "-")
        if dist not in declared:
            missing.append(f"{imp} (dist: {dist})")
    assert not missing, (
        "These packages are imported by drydock/ but NOT declared in "
        f"pyproject dependencies (would break a clean pip install): {missing}. "
        f"Declared: {sorted(declared)}"
    )
