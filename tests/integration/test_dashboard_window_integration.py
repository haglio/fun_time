"""Real-window checks for the dashboard's native title-bar chrome.

These build a real ``DashboardWindow`` and read its Win32 window styles straight off
``winId()`` — the taskbar/tool-window flags and the minimize/close/maximize buttons are
applied with ``ctypes.SetWindowLongW`` on the native HWND (see ``build_dashboard_window``).
That only works on the real Qt windows platform, so the tests live in the integration
suite (which runs on the native platform) rather than the offscreen-by-default unit
suite, where ``winId()`` is not a real top-level HWND and the styles read back as garbage.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtGui import QCloseEvent

from fun_time import load_config
from fun_time.dashboard_app import (
    DashboardLaunchGeometry,
    build_dashboard_window,
    load_dashboard_app_config,
)
from fun_time.dashboard_layout import Size
from fun_time.manifest import write_windows_bridge_manifest


pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="reads native Win32 window styles"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]


def test_dashboard_window_decorations_and_close_handler(cfg_path: Path):
    """Window must show in taskbar (WS_EX_APPWINDOW) and close handler writes exit."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass")
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
        window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        # Window decorations: visible on taskbar via WS_EX_APPWINDOW.
        hwnd = int(window.winId())
        ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        assert not (ex_style & 0x00000080), "WS_EX_TOOLWINDOW should NOT be set"
        assert ex_style & 0x00040000, "WS_EX_APPWINDOW should be set"

        # Close handler: closeEvent writes 'exit' to ahk_cmd.txt.
        ahk_cmd_file = manifest_path.parent / "ahk_cmd.txt"
        assert not ahk_cmd_file.exists(), "ahk_cmd.txt should not exist before close"
        window.closeEvent(QCloseEvent())
        assert ahk_cmd_file.exists(), "Close handler should have written ahk_cmd.txt"
        assert ahk_cmd_file.read_text(encoding="utf-8") == "exit"
    finally:
        window.close()


def test_dashboard_window_shows_native_minimize_and_close_buttons(cfg_path: Path):
    """Top-right title-bar controls: minimize + close, but no maximize."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass")
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
        window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        hwnd = int(window.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
        assert style & 0x00080000, "WS_SYSMENU must be set (enables the close button)"
        assert style & 0x00020000, "WS_MINIMIZEBOX must be set (enables minimize)"
        assert not (style & 0x00010000), "WS_MAXIMIZEBOX must NOT be set (no maximize)"
    finally:
        window.close()
