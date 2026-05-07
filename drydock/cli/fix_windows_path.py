"""One-shot helper to add the per-user Python scripts dir to user PATH on Windows.

Pip installs `drydock.exe` and `drydock-acp.exe` into
`%APPDATA%\\Python\\PythonXY\\Scripts` when run as `pip install --user`.
That directory is not on `PATH` by default on Windows, so the user types
`drydock` and gets "command not found." This helper appends the
directory to the **user** PATH (HKCU\\Environment, no admin needed) and
returns. Open a fresh shell and `drydock` resolves.

Idempotent — if the directory is already on user PATH, prints a confirmation
and exits 0.

The Linux/macOS path is a no-op (returns 0 with a hint) so the same
`--fix-windows-path` flag is safe to invoke on any platform.
"""
from __future__ import annotations

import os
import platform
import sys
import sysconfig
from pathlib import Path


def _user_scripts_dir() -> Path | None:
    # On Windows with `pip install --user`, the per-user scripts dir is
    # what `sysconfig.get_path("scripts", "nt_user")` reports. Pip prints
    # this exact path in its post-install warning.
    try:
        path = sysconfig.get_path("scripts", "nt_user")
    except KeyError:
        return None
    if not path:
        return None
    return Path(path)


def _read_user_path() -> str:
    import winreg  # type: ignore[import-not-found]

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
        try:
            value, _ = winreg.QueryValueEx(key, "Path")
            return value
        except FileNotFoundError:
            return ""


def _write_user_path(new_path: str) -> None:
    import winreg  # type: ignore[import-not-found]

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        # REG_EXPAND_SZ preserves %VARS% if the user already had any.
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)


def _broadcast_environment_change() -> None:
    """Tell other processes that the environment changed.

    Without this, freshly-spawned shells may still inherit the old PATH
    until the user logs out. A WM_SETTINGCHANGE broadcast is best-effort —
    PowerShell sessions started after this call will see the new value.
    """
    try:
        import ctypes
        from ctypes import wintypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = wintypes.DWORD()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except Exception:
        pass  # best-effort; failure is harmless


def fix_windows_path() -> int:
    if platform.system() != "Windows":
        print(
            "--fix-windows-path is a Windows-only helper. On Linux/macOS the "
            "scripts dir from `pip install --user` is typically already on PATH "
            "(via ~/.local/bin) or trivially added to your shell rc.",
            file=sys.stderr,
        )
        return 0

    scripts_dir = _user_scripts_dir()
    if scripts_dir is None:
        print(
            "Could not determine the per-user scripts directory. Try running "
            "`python -m site --user-base` to find your user-base, then add "
            "`<user-base>\\Scripts` to PATH manually via System Properties.",
            file=sys.stderr,
        )
        return 1

    scripts_str = str(scripts_dir)
    drydock_exe = scripts_dir / "drydock.exe"
    if not drydock_exe.exists():
        print(
            f"Warning: {drydock_exe} does not exist. The scripts dir is "
            f"{scripts_str} but drydock.exe is not in it — did you install "
            "with `pip install --user drydock-cli`? Continuing anyway.",
            file=sys.stderr,
        )

    try:
        current = _read_user_path()
    except OSError as e:
        print(f"Failed to read user PATH from registry: {e}", file=sys.stderr)
        return 1

    # Case-insensitive match — Windows paths aren't case-sensitive.
    existing_entries = [p for p in current.split(os.pathsep) if p]
    already_present = any(p.lower() == scripts_str.lower() for p in existing_entries)

    if already_present:
        print(f"OK — {scripts_str} is already on user PATH. Nothing to do.")
        return 0

    new_value = (current + os.pathsep + scripts_str) if current else scripts_str
    try:
        _write_user_path(new_value)
    except OSError as e:
        print(f"Failed to write user PATH: {e}", file=sys.stderr)
        return 1

    _broadcast_environment_change()

    print(f"Added {scripts_str} to user PATH.")
    print("Open a NEW PowerShell or Command Prompt window for the change to take effect.")
    print("Then run: drydock")
    return 0
