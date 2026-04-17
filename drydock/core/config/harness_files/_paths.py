from __future__ import annotations

from drydock.core.paths import DRYDOCK_HOME, GlobalPath

GLOBAL_TOOLS_DIR = GlobalPath(lambda: DRYDOCK_HOME.path / "tools")
GLOBAL_SKILLS_DIR = GlobalPath(lambda: DRYDOCK_HOME.path / "skills")
GLOBAL_AGENTS_DIR = GlobalPath(lambda: DRYDOCK_HOME.path / "agents")
GLOBAL_PROMPTS_DIR = GlobalPath(lambda: DRYDOCK_HOME.path / "prompts")
