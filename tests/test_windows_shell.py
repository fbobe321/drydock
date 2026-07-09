"""drydock runs natively on Windows via PowerShell/cmd — no WSL/bash required.
tool_bash picks the shell (POSIX→bash, Windows→PowerShell else cmd) and builds
the right invocation; the system prompt tells the model which shell it's on."""
from __future__ import annotations

import subprocess
from unittest import mock

import drydock.tools as T
from drydock import tuning


def test_posix_uses_bash():
    assert T._SHELL_KIND in ("bash", "sh")


def test_windows_prefers_powershell():
    with mock.patch.object(T, "_IS_WINDOWS", True), \
         mock.patch("shutil.which", side_effect=lambda n: r"C:\ps\pwsh.exe" if n == "pwsh" else None):
        kind, path = T._detect_shell()
    assert kind == "powershell" and path.endswith("pwsh.exe")


def test_windows_falls_back_to_cmd():
    with mock.patch.object(T, "_IS_WINDOWS", True), \
         mock.patch("shutil.which", side_effect=lambda n: r"C:\Windows\System32\cmd.exe" if n == "cmd" else None):
        kind, _ = T._detect_shell()
    assert kind == "cmd"


def test_powershell_invocation_argv(monkeypatch):
    """When the shell is PowerShell, Popen is called [pwsh -NoProfile
    -NonInteractive -Command <cmd>] with shell=False — not shell=True."""
    seen = {}

    def spy(popen_cmd, *a, **kw):
        seen["cmd"] = popen_cmd
        seen["shell"] = kw.get("shell")
        raise RuntimeError("stop-after-capture")  # don't actually run pwsh on Linux

    monkeypatch.setattr(T, "_SHELL_KIND", "powershell")
    monkeypatch.setattr(T, "_SHELL_PATH", r"C:\ps\pwsh.exe")
    monkeypatch.setattr(subprocess, "Popen", spy)
    T.tool_bash({"command": "Get-ChildItem", "timeout": 5}, {"cwd": "."})
    assert seen["shell"] is False
    assert seen["cmd"][:4] == [r"C:\ps\pwsh.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert seen["cmd"][4] == "Get-ChildItem"


def test_prompt_note_windows_powershell():
    with mock.patch("os.name", "nt"), \
         mock.patch("shutil.which", side_effect=lambda n: "x" if n in ("pwsh", "powershell") else None):
        assert "POWERSHELL" in tuning._shell_env_note()


def test_prompt_note_windows_cmd():
    with mock.patch("os.name", "nt"), mock.patch("shutil.which", return_value=None):
        assert "cmd.exe" in tuning._shell_env_note()


def test_prompt_note_empty_on_posix():
    assert tuning._shell_env_note() == ""


def test_env_override_forces_powershell():
    with mock.patch.dict("os.environ", {"DRYDOCK_SHELL": "powershell"}), \
         mock.patch("shutil.which", side_effect=lambda n: r"C:\pwsh.exe" if n in ("pwsh", "powershell") else None):
        assert T._detect_shell()[0] == "powershell"


def test_env_override_forces_cmd():
    with mock.patch.dict("os.environ", {"DRYDOCK_SHELL": "cmd"}), \
         mock.patch("shutil.which", return_value=r"C:\cmd.exe"):
        assert T._detect_shell()[0] == "cmd"


def test_env_override_forces_bash():
    with mock.patch.dict("os.environ", {"DRYDOCK_SHELL": "bash"}):
        assert T._detect_shell()[0] in ("bash", "sh")


def test_is_windows_detection_signals():
    from drydock.tools import _is_windows_env
    # native Windows
    assert _is_windows_env("nt", "win32", {})
    # MSYS/Git-Bash Python: posix os.name but WINDIR set
    assert _is_windows_env("posix", "msys", {"WINDIR": r"C:\Windows"})
    # Cygwin
    assert _is_windows_env("posix", "cygwin", {})
    # SystemRoot fallback
    assert _is_windows_env("posix", "linux", {"SystemRoot": r"C:\Windows"})
    # real Linux / WSL (no Windows env)
    assert not _is_windows_env("posix", "linux", {})
    assert not _is_windows_env("posix", "linux", {"HOME": "/home/x"})


def test_tool_display_name_maps_bash_to_shell():
    from unittest import mock
    for kind, expect in [("powershell", "PowerShell"), ("cmd", "cmd"), ("bash", "Bash"), ("sh", "sh")]:
        with mock.patch.object(T, "_SHELL_KIND", kind):
            assert T.tool_display_name("Bash") == expect
            assert T.tool_display_name("Read") == "Read"  # other tools unchanged
