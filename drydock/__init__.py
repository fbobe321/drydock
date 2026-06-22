"""DryDock — local coding agent."""
from importlib.metadata import PackageNotFoundError, version as _version

try:
    # Authoritative: the installed distribution's version. Keeps --version and
    # the TUI banner in sync with the published wheel automatically.
    __version__ = _version("drydock-cli")
except PackageNotFoundError:
    # Running from source before install (e.g. PYTHONPATH dev runs).
    __version__ = "3.0.24"
