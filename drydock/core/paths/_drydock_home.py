from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from drydock import DRYDOCK_ROOT


class GlobalPath:
    def __init__(self, resolver: Callable[[], Path]) -> None:
        self._resolver = resolver

    @property
    def path(self) -> Path:
        return self._resolver()


_DEFAULT_DRYDOCK_HOME = Path.home() / ".drydock"


def _get_drydock_home() -> Path:
    if drydock_home := os.getenv("DRYDOCK_HOME"):
        return Path(drydock_home).expanduser().resolve()
    return _DEFAULT_DRYDOCK_HOME


DRYDOCK_HOME = GlobalPath(_get_drydock_home)
GLOBAL_ENV_FILE = GlobalPath(lambda: DRYDOCK_HOME.path / ".env")
SESSION_LOG_DIR = GlobalPath(lambda: DRYDOCK_HOME.path / "logs" / "session")
TRUSTED_FOLDERS_FILE = GlobalPath(lambda: DRYDOCK_HOME.path / "trusted_folders.toml")
LOG_DIR = GlobalPath(lambda: DRYDOCK_HOME.path / "logs")
LOG_FILE = GlobalPath(lambda: DRYDOCK_HOME.path / "logs" / "drydock.log")
HISTORY_FILE = GlobalPath(lambda: DRYDOCK_HOME.path / "drydock_history")
PLANS_DIR = GlobalPath(lambda: DRYDOCK_HOME.path / "plans")

DEFAULT_TOOL_DIR = GlobalPath(lambda: DRYDOCK_ROOT / "core" / "tools" / "builtins")
