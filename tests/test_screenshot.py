"""Screenshot tool: capture the screen to PNG and make it visible to the vision
model (the returned path auto-attaches, same path as ViewImage). Cross-platform:
PowerShell on Windows, screencapture on macOS, common grabbers on Linux."""
from __future__ import annotations

import os
import tempfile
from unittest import mock

from drydock import tools, tool_registry
from drydock.providers import detect_image_paths
from drydock.tuning import filter_tool_schemas


def test_registered_and_available_to_gemma():
    names = [s["name"] for s in tool_registry.schemas()]
    gnames = [s["name"] for s in filter_tool_schemas(tool_registry.schemas(), "gemma4")]
    assert "Screenshot" in names and "Screenshot" in gnames


def test_graceful_when_no_grabber(monkeypatch):
    monkeypatch.setattr(tools, "_IS_WINDOWS", False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _n: None)  # no grabbers installed
    out = tools.tool_screenshot({}, {"cwd": os.getcwd()})
    assert out.startswith("Error") and "screenshot tool" in out


def test_success_returns_attachable_path():
    png = tempfile.mktemp(suffix=".png")

    def fake_cap(o):
        open(o, "wb").write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
        return None

    with mock.patch.object(tools, "_capture_screen", fake_cap):
        out = tools.tool_screenshot({"path": png}, {"cwd": os.getcwd()})
    assert "visible to you" in out
    assert detect_image_paths(out) == [os.path.abspath(png)]  # auto-attaches


def test_windows_builds_powershell_capture():
    calls = {}
    with mock.patch.object(tools, "_IS_WINDOWS", True), \
         mock.patch("shutil.which", side_effect=lambda n: r"C:\pwsh.exe" if n == "pwsh" else None), \
         mock.patch("subprocess.run", side_effect=lambda a, **k: calls.setdefault("argv", a)):
        assert tools._capture_screen(r"C:\shot.png") is None
    argv = calls["argv"]
    assert argv[0].endswith("pwsh.exe") and argv[1] == "-NoProfile"
    assert "CopyFromScreen" in argv[-1] and "ImageFormat]::Png" in argv[-1]


def test_macos_uses_screencapture(monkeypatch):
    calls = {}
    monkeypatch.setattr(tools, "_IS_WINDOWS", False)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("subprocess.run", lambda a, **k: calls.setdefault("argv", a))
    tools._capture_screen("/tmp/x.png")
    assert calls["argv"][0] == "screencapture"


def test_headless_linux_fails_fast(monkeypatch):
    import time
    monkeypatch.setattr(tools, "_IS_WINDOWS", False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    t = time.time()
    err = tools._capture_screen("/tmp/x.png")
    assert time.time() - t < 1.0            # no 30s grabber hang
    assert err and "display" in err.lower()
