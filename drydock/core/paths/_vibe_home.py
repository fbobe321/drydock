from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from drydock import VIBE_ROOT


class GlobalPath:
    def __init__(self, resolver: Callable[[], Path]) -> None:
        self._resolver = resolver

    @property
    def path(self) -> Path:
        return self._resolver()


_DEFAULT_DRYDOCK_HOME = Path.home() / ".drydock"
_DEFAULT_VIBE_HOME = Path.home() / ".vibe"

_migration_done = False


def _migrate_vibe_to_drydock() -> None:
    """Auto-migrate ~/.vibe → ~/.drydock if the user hasn't migrated yet."""
    global _migration_done
    if _migration_done:
        return
    _migration_done = True

    if not _DEFAULT_VIBE_HOME.exists() or _DEFAULT_DRYDOCK_HOME.exists():
        return  # Nothing to migrate, or already migrated

    import shutil
    import logging
    logger = logging.getLogger(__name__)

    try:
        shutil.copytree(_DEFAULT_VIBE_HOME, _DEFAULT_DRYDOCK_HOME)
        logger.info("Migrated ~/.vibe → ~/.drydock")
        # Leave a note in the old directory
        (_DEFAULT_VIBE_HOME / "MIGRATED.txt").write_text(
            "This config has been migrated to ~/.drydock/\n"
            "You can safely delete this .vibe directory.\n"
        )
    except (OSError, PermissionError) as e:
        logger.warning("Could not migrate ~/.vibe → ~/.drydock: %s", e)


def _get_vibe_home() -> Path:
    # Check env vars: DRYDOCK_HOME takes priority, then VIBE_HOME for compat
    if drydock_home := os.getenv("DRYDOCK_HOME"):
        return Path(drydock_home).expanduser().resolve()
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()

    # Auto-migrate ~/.vibe → ~/.drydock on first run
    _migrate_vibe_to_drydock()

    if _DEFAULT_DRYDOCK_HOME.exists():
        return _DEFAULT_DRYDOCK_HOME
    return _DEFAULT_DRYDOCK_HOME


VIBE_HOME = GlobalPath(_get_vibe_home)
GLOBAL_ENV_FILE = GlobalPath(lambda: VIBE_HOME.path / ".env")
SESSION_LOG_DIR = GlobalPath(lambda: VIBE_HOME.path / "logs" / "session")
TRUSTED_FOLDERS_FILE = GlobalPath(lambda: VIBE_HOME.path / "trusted_folders.toml")
LOG_DIR = GlobalPath(lambda: VIBE_HOME.path / "logs")
LOG_FILE = GlobalPath(lambda: VIBE_HOME.path / "logs" / "drydock.log")
HISTORY_FILE = GlobalPath(lambda: VIBE_HOME.path / "drydock_history")
PLANS_DIR = GlobalPath(lambda: VIBE_HOME.path / "plans")

DEFAULT_TOOL_DIR = GlobalPath(lambda: VIBE_ROOT / "core" / "tools" / "builtins")
