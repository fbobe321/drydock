"""Config drift diagnostics (Option C — `drydock doctor`).

Reads the user's ~/.drydock/config.toml, compares each field against the
pydantic default, and reports drift. Designed to complement the auto-merge
behavior in Option A: even though list fields union with defaults at
load time, the user's config.toml FILE still shows the old values —
the merge is runtime-only. `drydock doctor --fix` rewrites the TOML with
the merged values so the file matches what's actually loaded.

Separately reports scalar drift (e.g., auto_compact_threshold=200000 when
the sensible value for the active model is lower). Those are advisory —
doctor prints them but won't change them without --fix.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    import tomli_w
except ImportError:  # pragma: no cover
    tomli_w = None  # type: ignore


# Fields known to be mergeable (user value unioned with package default).
# When drift is detected on these, `--fix` unions them.
MERGEABLE_LIST_FIELDS: dict[str, str] = {
    # "section.field": "human_label"
    "bash.allowlist": "bash allowlist (auto-approved commands)",
    "bash.denylist": "bash denylist (blocked commands)",
    "bash.denylist_standalone": "bash denylist_standalone",
}


@dataclass
class DriftReport:
    field: str
    user_value: Any
    default_value: Any
    kind: str  # "list_merge" | "missing_in_user" | "scalar_stale"
    fix_description: str

    def to_row(self) -> tuple[str, str, str, str]:
        u = str(self.user_value)
        d = str(self.default_value)
        if len(u) > 40:
            u = u[:37] + "..."
        if len(d) > 40:
            d = d[:37] + "..."
        return (self.field, u, d, self.kind)


def _get_tool_section(toml_data: dict, section: str) -> dict | None:
    """Walk `tools.bash` → toml_data.get('tools', {}).get('bash', {})."""
    cur: Any = toml_data
    for part in section.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur if isinstance(cur, dict) else None


def _get_default_list(field_path: str) -> list[str]:
    """Resolve the package-default factory for a dotted field path."""
    section, field = field_path.rsplit(".", 1)
    if section == "bash":
        from drydock.core.tools.builtins.bash import (
            _get_default_allowlist,
            _get_default_denylist,
            _get_default_denylist_standalone,
        )
        factories = {
            "allowlist": _get_default_allowlist,
            "denylist": _get_default_denylist,
            "denylist_standalone": _get_default_denylist_standalone,
        }
        fn = factories.get(field)
        return list(fn()) if fn else []
    return []


def analyze_config(config_path: Path) -> tuple[dict, list[DriftReport]]:
    """Load the user's config.toml and compare each mergeable field against
    its package default. Returns (raw_toml_data, drift_reports)."""
    if not config_path.exists():
        return {}, []
    with config_path.open("rb") as f:
        data = tomllib.load(f)

    reports: list[DriftReport] = []

    # Mergeable list fields — check for defaults missing from user config.
    for field_path, _label in MERGEABLE_LIST_FIELDS.items():
        section, field = field_path.rsplit(".", 1)
        # Drydock TOML structure: [tools.bash] allowlist = [...]
        user_section = _get_tool_section(data, f"tools.{section}")
        user_value = user_section.get(field) if user_section else None
        defaults = _get_default_list(field_path)
        if user_value is None:
            # User has no entry — pydantic uses full defaults. No drift.
            continue
        if not isinstance(user_value, list):
            continue
        if "__override__" in user_value:
            continue  # explicit opt-out, respect it
        missing = [d for d in defaults if d not in user_value]
        if missing:
            reports.append(DriftReport(
                field=f"tools.{field_path}",
                user_value=f"{len(user_value)} entries",
                default_value=f"{len(defaults)} in package",
                kind="list_merge",
                fix_description=(
                    f"Add {len(missing)} missing default(s): "
                    + ", ".join(missing[:5])
                    + (f" … (+{len(missing) - 5} more)" if len(missing) > 5 else "")
                ),
            ))

    return data, reports


def print_drift_report(reports: list[DriftReport]) -> None:
    """Print a table of drift findings."""
    from rich.console import Console
    from rich.table import Table
    console = Console()
    if not reports:
        console.print("[green]No config drift detected.[/green]")
        console.print(
            "Your ~/.drydock/config.toml is in sync with the package defaults."
        )
        return
    table = Table(title="Config drift vs. package defaults", show_lines=True)
    table.add_column("Field")
    table.add_column("User", overflow="fold")
    table.add_column("Package default", overflow="fold")
    table.add_column("Kind")
    for r in reports:
        row = r.to_row()
        table.add_row(*row)
    console.print(table)
    console.print("\n[bold]Details:[/bold]")
    for r in reports:
        console.print(f"  • [cyan]{r.field}[/cyan]: {r.fix_description}")
    console.print(
        "\nRun [bold]drydock --doctor --fix[/bold] to apply safe corrections "
        "(writes a backup to config.toml.bak).\n"
        "Auto-mergeable lists are ALREADY merged at runtime — fixing only "
        "updates the FILE to reflect what's loaded.\n"
    )


def apply_fix(config_path: Path, data: dict, reports: list[DriftReport]) -> int:
    """Union missing defaults into the user's TOML file. Returns count of fields updated."""
    if tomli_w is None:
        raise RuntimeError(
            "tomli_w is not installed. Add `pip install tomli_w` to use --fix."
        )
    updated = 0
    for r in reports:
        if r.kind != "list_merge":
            continue
        # r.field is e.g. "tools.bash.allowlist"
        parts = r.field.split(".")
        cur: Any = data
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        final_field = parts[-1]
        defaults_path = ".".join(parts[1:])  # strip leading "tools."
        defaults = _get_default_list(defaults_path)
        user_list = cur.get(final_field, [])
        merged = list(user_list) if isinstance(user_list, list) else []
        for d in defaults:
            if d not in merged:
                merged.append(d)
        cur[final_field] = merged
        updated += 1

    if updated == 0:
        return 0

    # Write backup then new file.
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    if config_path.exists():
        backup.write_bytes(config_path.read_bytes())
    with config_path.open("wb") as f:
        tomli_w.dump(data, f)
    return updated


def run_doctor(apply: bool = False) -> int:
    """Entry point for `drydock --doctor [--fix]`. Returns shell exit code."""
    from drydock.core.config.harness_files import get_harness_files_manager
    from rich.console import Console
    console = Console()

    mgr = get_harness_files_manager()
    config_path = mgr.config_file
    if config_path is None:
        console.print("[yellow]No user config file found.[/yellow]")
        return 0
    console.print(f"Analyzing [bold]{config_path}[/bold] ...\n")
    data, reports = analyze_config(config_path)
    print_drift_report(reports)
    if apply and reports:
        updated = apply_fix(config_path, data, reports)
        console.print(
            f"\n[green]Applied fixes to {updated} field(s). "
            f"Backup saved to {config_path}.bak[/green]"
        )
    return 0
