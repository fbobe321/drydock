from __future__ import annotations

from importlib.metadata import version as _get_version
from pathlib import Path

DRYDOCK_ROOT = Path(__file__).parent
VIBE_ROOT = DRYDOCK_ROOT  # Backward compatibility alias

try:
    __version__ = _get_version("drydock-cli")
except Exception:
    __version__ = "dev"
