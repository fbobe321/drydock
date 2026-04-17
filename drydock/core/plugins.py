"""Plugin system for DryDock.

Plugins are directories with a plugin.json manifest that can provide:
- skills/ — Skill definitions (SKILL.md files)
- agents/ — Agent profiles (TOML or Markdown)
- hooks/ — Hook scripts

Install: drydock plugin install <path-or-url>
Location: ~/.drydock/plugins/<name>/

plugin.json format:
{
    "name": "my-plugin",
    "version": "1.0.0",
    "description": "What this plugin does",
    "author": "Name",
    "skills": ["skill-name"],
    "agents": ["agent-name"],
    "hooks": ["hook-config.json"]
}
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PluginInfo:
    name: str
    version: str
    description: str
    path: Path
    skills: list[str]
    agents: list[str]


def _plugins_dir() -> Path:
    """Get the plugins directory."""
    try:
        from drydock.core.paths import DRYDOCK_HOME
        return DRYDOCK_HOME.path / "plugins"
    except Exception:
        return Path.home() / ".drydock" / "plugins"


def list_plugins() -> list[PluginInfo]:
    """List all installed plugins."""
    plugins_dir = _plugins_dir()
    if not plugins_dir.is_dir():
        return []

    plugins: list[PluginInfo] = []
    for plugin_dir in sorted(plugins_dir.iterdir()):
        manifest = plugin_dir / "plugin.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text())
                plugins.append(PluginInfo(
                    name=data.get("name", plugin_dir.name),
                    version=data.get("version", "0.0.0"),
                    description=data.get("description", ""),
                    path=plugin_dir,
                    skills=data.get("skills", []),
                    agents=data.get("agents", []),
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Invalid plugin at %s: %s", plugin_dir, e)

    return plugins


def install_plugin(source: str | Path) -> PluginInfo | None:
    """Install a plugin from a local directory."""
    source_path = Path(source)
    if not source_path.is_dir():
        logger.error("Plugin source must be a directory: %s", source)
        return None

    manifest = source_path / "plugin.json"
    if not manifest.is_file():
        logger.error("No plugin.json found in %s", source)
        return None

    data = json.loads(manifest.read_text())
    name = data.get("name", source_path.name)

    dest = _plugins_dir() / name
    dest.mkdir(parents=True, exist_ok=True)

    # Copy plugin files
    shutil.copytree(source_path, dest, dirs_exist_ok=True)
    logger.info("Installed plugin '%s' to %s", name, dest)

    return PluginInfo(
        name=name,
        version=data.get("version", "0.0.0"),
        description=data.get("description", ""),
        path=dest,
        skills=data.get("skills", []),
        agents=data.get("agents", []),
    )


def uninstall_plugin(name: str) -> bool:
    """Uninstall a plugin by name."""
    dest = _plugins_dir() / name
    if dest.is_dir():
        shutil.rmtree(dest)
        logger.info("Uninstalled plugin '%s'", name)
        return True
    return False


def get_plugin_skill_dirs() -> list[Path]:
    """Get skill directories from all installed plugins."""
    dirs: list[Path] = []
    for plugin in list_plugins():
        skills_dir = plugin.path / "skills"
        if skills_dir.is_dir():
            dirs.append(skills_dir)
    return dirs


def get_plugin_agent_dirs() -> list[Path]:
    """Get agent directories from all installed plugins."""
    dirs: list[Path] = []
    for plugin in list_plugins():
        agents_dir = plugin.path / "agents"
        if agents_dir.is_dir():
            dirs.append(agents_dir)
    return dirs
