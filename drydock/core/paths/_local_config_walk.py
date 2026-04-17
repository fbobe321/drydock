from __future__ import annotations

from functools import cache
import os
from pathlib import Path

from drydock.core.autocompletion.file_indexer.ignore_rules import WALK_SKIP_DIR_NAMES

_DRYDOCK_DIR = ".drydock"
_AGENTS_DIR = ".agents"

_CONFIG_DIRS = (_DRYDOCK_DIR,)


@cache
def walk_local_config_dirs_all(
    root: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    tools_dirs: list[Path] = []
    skills_dirs: list[Path] = []
    agents_dirs: list[Path] = []
    resolved_root = root.resolve()
    for dirpath, dirnames, _ in os.walk(resolved_root, topdown=True):
        dir_set = frozenset(dirnames)
        path = Path(dirpath)
        for config_dir in _CONFIG_DIRS:
            if config_dir in dir_set:
                if (candidate := path / config_dir / "tools").is_dir():
                    tools_dirs.append(candidate)
                if (candidate := path / config_dir / "skills").is_dir():
                    skills_dirs.append(candidate)
                if (candidate := path / config_dir / "agents").is_dir():
                    agents_dirs.append(candidate)
                break
        if _AGENTS_DIR in dir_set:
            if (candidate := path / _AGENTS_DIR / "skills").is_dir():
                skills_dirs.append(candidate)
        dirnames[:] = sorted(d for d in dirnames if d not in WALK_SKIP_DIR_NAMES)
    return (tuple(tools_dirs), tuple(skills_dirs), tuple(agents_dirs))
