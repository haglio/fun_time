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
from fun_time.dashboard_actions import HELP_REFERENCE
from fun_time.dashboard_app import (
    REFERENCE_WINDOW_TITLE,
    DashboardLaunchGeometry,
    build_dashboard_window,
    load_dashboard_app_config,
)
from fun_time.dashboard_runtime import (
    DashboardSnapshot,
    DashboardWindowSnapshot,
)
from fun_time.event_log import NOTICE, event_log_path, notice
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.win32 import find_window_by_title, is_window_topmost
from fun_time.window_layout import MonitorRect, compute_window_layout


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
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

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
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        hwnd = int(window.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
        assert style & 0x00080000, "WS_SYSMENU must be set (enables the close button)"
        assert style & 0x00020000, "WS_MINIMIZEBOX must be set (enables minimize)"
        assert not (style & 0x00010000), "WS_MAXIMIZEBOX must NOT be set (no maximize)"
    finally:
        window.close()


def _build_merged_dashboard(cfg_path: Path):
    """Build the real dashboard at the rect production computes — one window that
    spans the whole left column, with the log stream embedded under the control bar.
    """
    from PyQt6.QtWidgets import QApplication

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )
    launch_geo = DashboardLaunchGeometry(
        plan.dashboard.x, plan.dashboard.y, plan.dashboard.width, plan.dashboard.height,
    )
    window = build_dashboard_window(app_config, launch_geometry=launch_geo)
    # Let the central layout distribute the strip to the embedded log widget so
    # its geometry is final before the tests read it.
    QApplication.processEvents()
    return window, manifest_path.parent


def test_log_stream_fills_the_window_under_the_control_bar(cfg_path: Path):
    """The top row runs along the top and the log takes everything beneath it,
    full width — where it used to be a strip beside a drawing of the monitors."""
    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        top_row = window._widget.parentWidget()
        log = window._log_widget
        central = window.centralWidget()
        # The log stream and the log's filter controls are separate now: the
        # controls rode up into the top row beside the bar, so the Dash is a row
        # shorter and only the stream sits below.
        assert window._log_widget.controls.parentWidget() is top_row
        # The log starts at the top row's bottom edge ...
        assert log.y() == top_row.y() + top_row.height()
        # ... spans the window rather than a strip of it ...
        assert log.width() == central.width()
        # ... and the two together fill the window's client height.
        assert top_row.height() + log.height() == central.height()
        assert log.height() > top_row.height()
    finally:
        window.close()


def test_log_controls_fit_one_row_beside_the_bar_and_lines_word_wrap(cfg_path: Path):
    """The verbosity dial and source toggles share a single row that fits inside
    the space the top bar leaves them (real font metrics enforce a minimum the
    offscreen platform never does), and long log lines wrap instead of being cut
    off with an ellipsis."""
    from PyQt6.QtCore import Qt

    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        panel = window._log_widget
        controls_width = panel.controls.width()
        # The last source toggle's right edge stays inside the controls' own
        # width: nothing is pushed off it, so it is genuinely one row that fits.
        last = panel._source_boxes["system"]
        assert last.x() + last.width() <= controls_width
        assert panel.controls.minimumSizeHint().width() <= controls_width
        # Long lines wrap rather than elide.
        assert panel._list.wordWrap()
        assert panel._list.textElideMode() == Qt.TextElideMode.ElideNone
    finally:
        window.close()


def test_a_notice_in_the_event_log_flashes_over_the_player_it_is_for(cfg_path: Path):
    """End to end over a real file: a NOTICE the dispatch loop would emit is
    picked up by the dashboard's tail and flashed, at the top-center of the
    window its source names — the overlay that replaced the AHK tooltip."""
    import logging

    from fun_time.event_log import EventLogHandler

    window, state_dir = _build_merged_dashboard(cfg_path)
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
        # A normal notice reads green; a dead-end (ERROR) reads red.  The flash
        # color is applied by stylesheet, so assert against level_color directly.
        from fun_time.log_panel import level_color

        assert level_color(NOTICE).name() in overlay.styleSheet()
        notice(writer, "No other seeds", source="portrait", level=logging.ERROR)
        window._poll_notices()
        assert overlay.text() == "No other seeds"
        assert level_color(logging.ERROR).name() in overlay.styleSheet()
        assert level_color(NOTICE).name() != level_color(logging.ERROR).name()
    finally:
        window.close()


def _omnipause_snapshot(*, omni_paused: bool) -> DashboardSnapshot:
    """The state file's snapshot, as the dashboard's refresh reads it."""
    return DashboardSnapshot(
        omni_paused=omni_paused,
        window=DashboardWindowSnapshot(x=0, y=0, width=0, height=0),
    )


def test_omnipause_drops_the_reference_popup_from_the_topmost_band(cfg_path: Path):
    """The hotkeys & voice reference must free the desktop with everything else.

    It is a top-level window of its own — not one of the bridge's managed roles
    the orchestrator can drop, and not a child riding the dashboard's band — so
    nothing took it out of WS_EX_TOPMOST and it stayed pinned over the desktop
    for the whole pause.  Read off the real HWND: only the native platform gives
    the popup a top-level window whose ex-style means anything.
    """
    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        window._on_action(HELP_REFERENCE)  # the ? button's own path
        dialog = window._reference_dialog
        assert dialog is not None
        hwnd = int(dialog.winId())
        # The window we are banding is the one the user sees by name.
        assert find_window_by_title(REFERENCE_WINDOW_TITLE, exact=True) == hwnd
        # It floats over the players while the desktop is live.
        assert is_window_topmost(hwnd), "the popup should open topmost"

        window._do_render(_omnipause_snapshot(omni_paused=True), frozenset())
        assert not is_window_topmost(hwnd), "OmniPause must free the desktop of it"

        window._do_render(_omnipause_snapshot(omni_paused=False), frozenset())
        assert is_window_topmost(hwnd), "leaving OmniPause floats it back on top"
    finally:
        window.close()


def test_the_reference_popup_opens_non_topmost_under_omnipause(cfg_path: Path):
    """Qt applies StaysOnTop as the popup is shown, so opening it mid-pause would
    strand it over the freed desktop until the next refresh corrected it — it is
    banded at open time instead, off the last snapshot."""
    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        window._do_render(_omnipause_snapshot(omni_paused=True), frozenset())

        window._on_action(HELP_REFERENCE)
        dialog = window._reference_dialog
        assert dialog is not None
        assert not is_window_topmost(int(dialog.winId()))
    finally:
        window.close()


def test_closing_the_dashboard_stops_its_pollers_and_the_log_tail(cfg_path: Path):
    """A dashboard left running keeps polling the players' status files twice a
    second and holds a UDP socket.  Several dashboards are built and closed
    inside this one pytest process, so a leaked poller would keep reading — and
    keep its socket bound — for the rest of the run.  The embedded log widget's
    tail must stop with them."""
    window, _state_dir = _build_merged_dashboard(cfg_path)
    log = window._log_widget
    overlay = window._notice_overlay

    window.close()

    assert window._stopping.is_set()
    assert not window._refresh_timer.isActive()
    assert not window._notice_timer.isActive()
    assert not log._timer.isActive()
    assert window._notice_overlay is None
    if overlay is not None:
        assert not overlay.isVisible()
