from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication
from shared_ui.spacing import MARGIN_STANDARD

from fun_time import load_config
from fun_time.dashboard_actions import HELP_REFERENCE
from fun_time.dashboard_app import (
    REFERENCE_WINDOW_TITLE,
    DashboardLaunchGeometry,
    build_dashboard_window,
    load_dashboard_app_config,
)
from fun_time.dashboard_layout import RFB_MODE_SELECTOR_HEIGHT
from fun_time.dashboard_runtime import DashboardSnapshot
from fun_time.event_log import MESSAGE, EventLogHandler, event_log_path, flash_message
from fun_time.log_panel import level_color
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
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        hwnd = int(window.winId())
        ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        assert not (ex_style & 0x00000080), "WS_EX_TOOLWINDOW should NOT be set"
        assert ex_style & 0x00040000, "WS_EX_APPWINDOW should be set"

        ahk_cmd_file = manifest_path.parent / "ahk_cmd.txt"
        assert not ahk_cmd_file.exists(), "ahk_cmd.txt should not exist before close"
        window.closeEvent(QCloseEvent())
        assert ahk_cmd_file.exists(), "Close handler should have written ahk_cmd.txt"
        assert ahk_cmd_file.read_text(encoding="utf-8") == "end_session"
    finally:
        window.close()


def test_dashboard_window_shows_native_minimize_and_close_buttons(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        hwnd = int(window.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
        assert style & 0x00080000, "WS_SYSMENU must be set (enables the close button)"
        assert style & 0x00020000, "WS_MINIMIZEBOX must be set (enables minimize)"
        assert not (style & 0x00010000), "WS_MAXIMIZEBOX must NOT be set (no maximize)"
    finally:
        window.close()


def _build_merged_dashboard(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
        rfb_mode_selector=app_config.rfb_mode_selector,
    )
    launch_geo = DashboardLaunchGeometry(
        plan.dashboard.x, plan.dashboard.y, plan.dashboard.width, plan.dashboard.height,
    )
    window = build_dashboard_window(app_config, launch_geometry=launch_geo)
    QApplication.processEvents()
    return window, manifest_path.parent


def test_log_stream_fills_the_column_under_the_control_bar(cfg_path: Path):
    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        bar, log, osr2 = window._widget, window._log_widget, window._osr2
        central = window.centralWidget()
        assert log.y() == bar.y() + bar.height()
        assert log.width() == osr2.x()
        assert (osr2.x() + osr2.width(), osr2.y()) == (central.width(), 0)
        assert bar.height() + log.height() == central.height()
        assert log.height() > bar.height()
    finally:
        window.close()


def test_in_a_session_hosting_origenerator_the_strip_takes_the_columns_lower_edge(
        cfg_factory, tmp_path: Path):
    (tmp_path / "origenerator").mkdir()
    window, _state_dir = _build_merged_dashboard(
        cfg_factory({"paths": {"origenerator_dir": str(tmp_path / "origenerator")}}))
    try:
        central, log, strip = window.centralWidget(), window._log_widget, window._mode_selector
        assert strip.height() == RFB_MODE_SELECTOR_HEIGHT
        assert strip.width() == log.width()
        assert strip.y() == log.y() + log.height()
        assert strip.y() + strip.height() == central.height()
    finally:
        window.close()


def test_log_filters_fit_one_row_inside_the_log_panel_and_lines_word_wrap(cfg_path: Path):
    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        panel = window._log_widget
        last = panel._source_buttons["system"]
        assert last.y() == panel._verbosity.y()
        assert last.x() + last.width() <= panel.width() - MARGIN_STANDARD
        assert panel._list.wordWrap()
        assert panel._list.textElideMode() == Qt.TextElideMode.ElideNone
    finally:
        window.close()


def test_a_message_in_the_event_log_flashes_over_the_funestra_it_is_for(cfg_path: Path):
    window, state_dir = _build_merged_dashboard(cfg_path)
    try:
        writer = logging.getLogger("integration.event_log.writer")
        writer.handlers.clear()
        writer.propagate = False
        writer.setLevel(MESSAGE)
        writer.addHandler(EventLogHandler(event_log_path(state_dir)))

        flash_message(writer, "Clip saved", source="portrait")
        window._messages.poll()

        overlay = window._messages.overlay
        assert overlay is not None
        assert overlay.isVisible()
        assert overlay.text() == "Clip saved"
        portrait = window._messages.funestra_rects.portrait
        assert portrait.x <= overlay.x() <= portrait.x + portrait.width
        assert overlay.y() < portrait.y + portrait.height // 2
        assert level_color(MESSAGE).name() in overlay.styleSheet()
        flash_message(writer, "No other seeds", source="portrait", level=logging.WARNING)
        window._messages.poll()
        assert overlay.text() == "No other seeds"
        assert level_color(logging.WARNING).name() in overlay.styleSheet()
        assert level_color(MESSAGE).name() != level_color(logging.WARNING).name()
    finally:
        window.close()


def _omnipause_snapshot(*, omni_paused: bool) -> DashboardSnapshot:
    return DashboardSnapshot(omni_paused=omni_paused)


def test_omnipause_drops_the_reference_popup_from_the_topmost_band(cfg_path: Path):
    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        window._on_action(HELP_REFERENCE)
        dialog = window._reference.dialog
        assert dialog is not None
        hwnd = int(dialog.winId())
        assert find_window_by_title(REFERENCE_WINDOW_TITLE, exact=True) == hwnd
        assert is_window_topmost(hwnd), "the popup should open topmost"

        window._do_render(_omnipause_snapshot(omni_paused=True), frozenset())
        assert not is_window_topmost(hwnd), "OmniPause must free the desktop of it"

        window._do_render(_omnipause_snapshot(omni_paused=False), frozenset())
        assert is_window_topmost(hwnd), "leaving OmniPause floats it back on top"
    finally:
        window.close()


def test_the_reference_popup_opens_non_topmost_under_omnipause(cfg_path: Path):
    window, _state_dir = _build_merged_dashboard(cfg_path)
    try:
        window._do_render(_omnipause_snapshot(omni_paused=True), frozenset())

        window._on_action(HELP_REFERENCE)
        dialog = window._reference.dialog
        assert dialog is not None
        assert not is_window_topmost(int(dialog.winId()))
    finally:
        window.close()


def test_closing_the_dashboard_stops_its_pollers_and_the_log_tail(cfg_path: Path):
    window, _state_dir = _build_merged_dashboard(cfg_path)
    log = window._log_widget
    overlay = window._messages.overlay

    window.close()

    assert not window._press_channel.listening
    assert not any(timer.isActive() for timer in window.findChildren(QTimer))
    assert not log._timer.isActive()
    assert not window._osr2._timer.isActive()
    assert window._messages.overlay is None
    if overlay is not None:
        assert not overlay.isVisible()
