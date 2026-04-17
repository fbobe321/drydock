from __future__ import annotations

from importlib.metadata import version as _get_version
from pathlib import Path

DRYDOCK_ROOT = Path(__file__).parent

try:
    __version__ = _get_version("drydock-cli")
except Exception:
    __version__ = "dev"
