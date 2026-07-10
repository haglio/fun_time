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
from fun_time.dashboard_layout import Rect, Size
from fun_time.event_log import NOTICE, event_log_path, notice
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.window_layout import MonitorRect, compute_window_layout
from fun_time.window_roles import LOG_PANEL_WINDOW_TITLE
from fun_time.win32 import find_window_by_title


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


def _build_window_with_log_panel(cfg_path: Path):
    """Build the real dashboard + log panel at the rects production computes."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass")
    app_config = load_dashboard_app_config(manifest_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )
    launch_geo = DashboardLaunchGeometry(
        plan.dashboard.x, plan.dashboard.y, plan.dashboard.width, plan.dashboard.height,
    )
    log_rect = Rect(
        plan.log_panel.x, plan.log_panel.y, plan.log_panel.width, plan.log_panel.height,
    )
    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
        window = build_dashboard_window(app_config, launch_geometry=launch_geo, log_panel_rect=log_rect)
    return window, manifest_path.parent, log_rect


def test_log_panel_window_is_resolvable_by_its_exact_title(cfg_path: Path):
    """The dispatch loop reaches the panel by title alone — it shares the
    dashboard's pid — so omnipause and omniminimize hang on this lookup working
    against the real window.
    """
    window, _state_dir, _log_rect = _build_window_with_log_panel(cfg_path)
    try:
        panel_hwnd = int(window._log_panel.winId())

        assert find_window_by_title(LOG_PANEL_WINDOW_TITLE, exact=True) == panel_hwnd
        assert find_window_by_title("Fun Time", exact=True) != panel_hwnd
    finally:
        window.close()


def test_log_panel_fills_the_strip_beside_the_dashboard(cfg_path: Path):
    window, _state_dir, log_rect = _build_window_with_log_panel(cfg_path)
    try:
        geo = window._log_panel.geometry()
        assert (geo.x(), geo.y()) == (log_rect.x, log_rect.y)
        assert (geo.width(), geo.height()) == (log_rect.width, log_rect.height)
    finally:
        window.close()


def test_a_notice_in_the_event_log_flashes_over_the_player_it_is_for(cfg_path: Path):
    """End to end over a real file: a NOTICE the dispatch loop would emit is
    picked up by the dashboard's tail and flashed, at the top-center of the
    window its source names — the overlay that replaced the AHK tooltip."""
    import logging

    from fun_time.event_log import EventLogHandler

    window, state_dir, _log_rect = _build_window_with_log_panel(cfg_path)
    try:
        writer = logging.getLogger("integration.event_log.writer")
        writer.handlers.clear()
        writer.propagate = False
        writer.setLevel(NOTICE)
        writer.addHandler(EventLogHandler(event_log_path(state_dir)))

        notice(writer, "Clip saved", source="portrait")
        window._poll_notices()

        overlay = window._notice_overlay
        assert overlay is not None
        assert overlay.isVisible()
        assert overlay.text() == "Clip saved"
        # Centered across the portrait player's top, not the dashboard's.
        portrait = window._player_rects.portrait
        assert portrait.x <= overlay.x() <= portrait.x + portrait.width
        assert overlay.y() < portrait.y + portrait.height // 2
    finally:
        window.close()


def test_closing_the_dashboard_stops_its_pollers_and_disposes_the_panel(cfg_path: Path):
    """A dashboard left running keeps polling VLC's HTTP interface twice a second
    and holds a UDP socket.  Several dashboards are built and closed inside this
    one pytest process, so a leaked poller would pile connections onto whichever
    VLC holds those ports for the rest of the run."""
    window, _state_dir, _log_rect = _build_window_with_log_panel(cfg_path)
    panel = window._log_panel
    overlay = window._notice_overlay
    assert panel is not None

    window.close()

    assert window._stopping.is_set()
    assert not window._refresh_timer.isActive()
    assert not window._notice_timer.isActive()
    assert not panel._timer.isActive()
    assert window._log_panel is None
    assert window._notice_overlay is None
    if overlay is not None:
        assert not overlay.isVisible()
    assert find_window_by_title(LOG_PANEL_WINDOW_TITLE, exact=True) == 0
