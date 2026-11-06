from __future__ import annotations

import ctypes
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app_support import file_channel
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QTextBrowser, QWidget
from shared_ui.colors import (
    BG_BUTTON,
    BG_BUTTON_ACTIVE,
    BLUE,
    GREEN,
    TEXT_MUTED,
    TEXT_PRIMARY,
    hovered,
)
from shared_ui.find_bar import FindBar
from shared_ui.icon_geometry import tooltip_for
from shared_ui.icons import icon_pixmap
from shared_ui.palette import LOADING_ACCENT, PREVIEW_INK, as_hex
from shared_ui.preview import Preview
from shared_ui.spacing import BUTTON_ICON_INSET, BUTTON_RADIUS_HUD, MARGIN_STANDARD
from shared_ui.testing import icons_lacking
from voice_core.commands import Recognition
from voice_core.listening import Heard

from fun_time import dashboard_app, load_config, preview_marker
from fun_time.command_value import parse_command
from fun_time.config import VoiceControlConfig
from fun_time.crown import Crown
from fun_time.dashboard_actions import (
    ENTER_VR,
    FMODE_TOGGLE,
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    RESET_ALL,
    VOICE_TOGGLE,
)
from fun_time.dashboard_app import (
    _BUTTON_RADIUS,
    COLOR_PANEL,
    OMNIPAUSE_RESUME_TOOLTIP,
    SOURCE_CHECKOUT_FILENAME,
    DashboardLaunchGeometry,
    DashboardWidget,
    IconCache,
    ReferenceDialog,
    RfbModeSelectorWidget,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
    build_dashboard_window,
    lighten_color,
    load_dashboard_app_config,
    record_source_checkout,
    source_checkout,
    write_dashboard_command,
)
from fun_time.dashboard_controls import BarState
from fun_time.dashboard_layout import (
    GAP,
    GROUP_GAP,
    LOG_LIST_HEIGHT,
    RFB_MODE_SELECTOR_HEIGHT,
    Rect,
    compute_dashboard_bar_layout,
    dashboard_window_height,
)
from fun_time.dashboard_runtime import DashboardSnapshot
from fun_time.event_log import (
    EVENT_LOG_FILENAME,
    MESSAGE,
    SOURCE_DASH,
    EventLogHandler,
    event_log_path,
)
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.monitors import MonitorInfo, get_logical_monitor_rects
from fun_time.origenerator_switches import RfbMode
from fun_time.press_flash import PRESS_FLASH_S
from fun_time.project_paths import PROJECT_ICON
from fun_time.satellite_buttons import STILL_STARTING_TOOLTIP
from fun_time.shared_state import BridgeState, shared_state_path, write_shared_state
from fun_time.unlogged_messages import flash_unlogged
from fun_time.voice_control import VoiceController
from fun_time.window_layout import (
    compute_main_media_rect,
    compute_window_layout,
    secondary_monitor_rects,
)
from fun_time.windows_bridge_dispatch_loop import poll_dashboard_commands
from tests.held_files import held_by_another_program, let_go_soon
from tests.integration.integration_support import environment_with_this_checkouts_siblings
from tests.made_up_osr2_parts import a_part, center_of, published
from tests.sleeps import sleeps_in


def _scene(snapshot: DashboardSnapshot | None = None, **kwargs):
    layout = compute_dashboard_bar_layout()
    kwargs.setdefault("icons", IconCache())
    state = BarState.of(snapshot, kwargs.pop("reference_open", False))
    return build_dashboard_scene(layout, state, **kwargs)


def _snapshot(**overrides) -> DashboardSnapshot:
    return DashboardSnapshot(**{"omni_paused": False, **overrides})


@pytest.fixture
def dashboard_app_config(cfg_path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    return load_dashboard_app_config(manifest_path)


@pytest.fixture
def dashboard_window(dashboard_app_config):
    window = build_dashboard_window(
        dashboard_app_config,
        launch_geometry=DashboardLaunchGeometry(x=100, y=200, width=300, height=400),
    )
    try:
        yield window
    finally:
        window.close()


def _lay_out(widget: QWidget) -> None:
    if widget.layout() is not None:
        widget.layout().activate()
    for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        _lay_out(child)


def _placed_where_a_session_places_it(app_config):
    plan = compute_window_layout(
        primary_monitor=Rect(0, 0, 2560, 1392), secondary_monitor=Rect(2560, 0, 1440, 3440),
        layout_config=app_config.layout, rfb_mode_selector=app_config.rfb_mode_selector)
    window = build_dashboard_window(app_config, launch_geometry=plan.dashboard)
    _lay_out(window)
    return window


def _at(window, widget) -> QPoint:
    return widget.mapTo(window, QPoint(0, 0))


@pytest.fixture
def placed_window(dashboard_app_config):
    window = _placed_where_a_session_places_it(dashboard_app_config)
    try:
        yield window
    finally:
        window.close()


def test_the_dashboard_window_wears_the_sessions_button_before_it_is_shown(
        dashboard_app_config, monkeypatch):
    shown_when_dressed = {}

    def dress(hwnd):
        window, = (top for top in QApplication.topLevelWidgets()
                   if top.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
                   and int(top.winId()) == hwnd)
        shown_when_dressed[hwnd] = window.isVisible()

    monkeypatch.setattr("fun_time.preview_marker.dress_the_window", dress)

    window = build_dashboard_window(
        dashboard_app_config,
        launch_geometry=DashboardLaunchGeometry(x=100, y=200, width=300, height=400),
    )
    try:
        assert shown_when_dressed == {int(window.winId()): False}
    finally:
        window.close()


def _fill(scene, rect):
    return next(item.fill for item in scene.rects if item.rect == rect)


def test_the_bar_carries_only_what_belongs_to_no_funestra():
    scene = _scene()

    assert [action for action, _rect in scene.actions] == [
        QUIT_BUTTON, OMNIPAUSE_TOGGLE, HELP_REFERENCE, VOICE_TOGGLE,
        FMODE_TOGGLE, RESET_ALL, ENTER_VR,
    ]


def test_the_every_funestra_pair_and_the_crossing_each_stand_in_a_group_of_their_own():
    layout = compute_dashboard_bar_layout()

    assert layout.fmode_button.x - (layout.voice_panel.x + layout.voice_panel.width) == GROUP_GAP
    assert (layout.vr_button.x
            - (layout.reset_all_button.x + layout.reset_all_button.width)) == GROUP_GAP


def test_reset_all_stands_beside_f_mode_in_its_group():
    layout = compute_dashboard_bar_layout()

    assert layout.reset_all_button.x - (layout.fmode_button.x + layout.fmode_button.width) == GAP


def test_the_bars_reset_all_resets_every_funestra():
    layout = compute_dashboard_bar_layout()
    scene = _scene()

    assert (RESET_ALL, layout.reset_all_button) in scene.actions
    assert [each.text for each in parse_command(RESET_ALL).each()] == ["main_reset", "portrait_reset", "landscape_reset"]
    assert any(item.rect == layout.reset_all_button for item in scene.images)
    assert dict(scene.hover_texts)[
        layout.reset_all_button].startswith("Reset")


def test_reset_all_takes_no_press_but_still_names_itself_when_every_funestra_is_at_its_defaults():
    layout = compute_dashboard_bar_layout()
    idle = _scene(_snapshot(nothing_to_reset=True))

    assert all(rect != layout.reset_all_button for _action, rect in idle.actions)
    assert (RESET_ALL, layout.reset_all_button) in _scene(_snapshot()).actions
    assert dict(idle.hover_texts)[layout.reset_all_button].startswith("Reset")


def test_the_bar_is_only_as_wide_as_its_own_buttons():
    layout = compute_dashboard_bar_layout()

    assert _scene().width == layout.width
    assert _scene().height == layout.height


def test_the_log_filters_sit_in_the_log_panel_under_the_bar(placed_window):
    dial = _at(placed_window, placed_window._log_widget._verbosity)

    assert dial == QPoint(MARGIN_STANDARD, compute_dashboard_bar_layout().height + MARGIN_STANDARD)


def test_the_filter_row_makes_the_window_taller_rather_than_the_list_shorter(placed_window):
    assert placed_window._log_widget._list.height() == LOG_LIST_HEIGHT


def test_the_window_is_the_bar_and_the_log_under_it():
    layout = compute_dashboard_bar_layout()

    assert dashboard_window_height() > layout.height
    assert dashboard_window_height() < 400


def test_the_app_names_itself_at_the_head_of_the_bar():
    scene = _scene()
    layout = compute_dashboard_bar_layout()

    title = next(item for item in scene.texts if item.text == "Fun Time")
    assert title.rect == layout.app_title
    assert title.color == QColor(*LOADING_ACCENT)
    assert any(item.rect == layout.app_icon for item in scene.images)
    assert all(rect != layout.app_title for rect, _text in scene.hover_texts)


def test_a_branch_session_names_itself_in_the_preview_ink_and_says_what_it_demos(
        monkeypatch, tmp_path):
    monkeypatch.setattr(preview_marker, "INKED_ICON_FOLDER", tmp_path)
    scene = _scene(shown_as=Preview(feature="the new reference popup"))
    layout = compute_dashboard_bar_layout()

    title = next(item for item in scene.texts if item.text == "Fun Time")
    assert title.color == QColor(as_hex(PREVIEW_INK))
    assert (layout.app_title, "Fun Time \u2014 preview of the new reference popup") in scene.hover_texts
    icon = next(item for item in scene.images if item.rect == layout.app_icon)
    middle = icon.pixmap.toImage().pixelColor(icon.pixmap.width() // 2, icon.pixmap.height() // 2)
    assert (middle.red(), middle.green(), middle.blue()) == PREVIEW_INK


def test_the_pause_button_says_which_way_it_will_go():
    layout = compute_dashboard_bar_layout()


    def its_icon(paused: bool):
        scene = _scene(_snapshot(omni_paused=paused))
        return next(i.pixmap for i in scene.images if i.rect == layout.omnipause_button)

    edge = _icon_side(layout.omnipause_button)
    assert its_icon(False).toImage() == icon_pixmap("pause", edge, TEXT_PRIMARY).toImage()
    assert its_icon(True).toImage() == icon_pixmap("play", edge, TEXT_PRIMARY).toImage()


def test_pausing_everything_does_not_paint_the_button_a_state_color():
    layout = compute_dashboard_bar_layout()

    assert _fill(_scene(_snapshot(omni_paused=True)), layout.omnipause_button) == COLOR_PANEL
    assert _fill(_scene(), layout.quit_button) == COLOR_PANEL


def test_the_microphone_comes_on_with_what_it_reports():
    layout = compute_dashboard_bar_layout()

    assert _fill(_scene(_snapshot(voice_active=True)), layout.voice_panel) == BLUE
    assert _fill(_scene(_snapshot(voice_active=False)), layout.voice_panel) == COLOR_PANEL


def test_the_microphone_carries_its_own_mark():
    layout = compute_dashboard_bar_layout()
    drawn = {item.rect for item in _scene().images}

    assert layout.voice_panel in drawn


def test_the_microphone_is_the_one_the_family_shares():
    panel = compute_dashboard_bar_layout().voice_panel
    drawn = {item.rect: item.pixmap for item in _scene().images}

    expected = icon_pixmap("mic", _icon_side(panel), TEXT_PRIMARY)
    assert drawn[panel].toImage() == expected.toImage()


def test_every_control_names_itself_on_hover():
    scene = _scene()

    assert {rect for rect, _text in scene.hover_texts} == {rect for _a, rect in scene.actions}
    assert all(text for _rect, text in scene.hover_texts)


def test_a_control_whose_mark_this_version_lacks_says_why_on_hover():
    with icons_lacking("power"):
        hover = dict(_scene().hover_texts)[compute_dashboard_bar_layout().quit_button]

        assert hover == tooltip_for("power", "Quit")


def test_a_pressed_control_lightens_while_the_press_shows():
    layout = compute_dashboard_bar_layout()

    resting = _fill(_scene(), layout.quit_button)
    pressed = _fill(_scene(pressed_actions=frozenset({QUIT_BUTTON})), layout.quit_button)

    assert resting == BG_BUTTON
    assert pressed == BG_BUTTON_ACTIVE


def test_a_control_wearing_a_state_color_lightens_that_color_instead():
    layout = compute_dashboard_bar_layout()

    pressed = _fill(_scene(_snapshot(voice_active=True),
                           pressed_actions=frozenset({VOICE_TOGGLE})),
                    layout.voice_panel)

    assert pressed == lighten_color(BLUE)


def test_the_config_reads_only_what_the_bar_needs(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, tmp_path / "manifest.ini")

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.layout.primary_monitor == config.layout.primary_monitor
    assert app_config.dashboard_cmd_file.name == "dashboard_cmd.txt"


def test_write_dashboard_command_retries_past_a_transient_file_lock(tmp_path: Path):
    command_file = tmp_path / "state" / "dashboard_cmd.txt"
    command_file.parent.mkdir(parents=True)

    with held_by_another_program(command_file) as let_go:
        let_go_soon(let_go)
        write_dashboard_command(command_file, "quit")

    assert command_file.read_text(encoding="utf-8").strip() == "quit"


def test_write_dashboard_command_drops_rather_than_raises_when_locked(tmp_path: Path):
    command_file = tmp_path / "state" / "dashboard_cmd.txt"
    command_file.parent.mkdir(parents=True)

    with held_by_another_program(command_file), sleeps_in(file_channel):
        write_dashboard_command(command_file, "quit")

    assert command_file.read_text(encoding="utf-8") == ""


def test_write_dashboard_command_queues_rather_than_clobbers(tmp_path: Path):
    command_file = tmp_path / "state" / "dashboard_cmd.txt"

    write_dashboard_command(command_file, "portrait_lock")
    write_dashboard_command(command_file, "quit")

    assert poll_dashboard_commands(command_file) == ["portrait_lock", "quit"]


def test_dashboard_window_geometry_prefers_launch_geometry_when_provided():
    scene = _scene()

    widget = QWidget()
    apply_dashboard_window_geometry(
        widget,
        scene,
        launch_geometry=DashboardLaunchGeometry(x=11, y=22, width=333, height=444),
    )
    geo = widget.geometry()

    assert (geo.x(), geo.y(), geo.width(), geo.height()) == (11, 22, 333, 444)


def _window_under_the_loading_screen(app_config, **placed):
    with patch("fun_time.loading_reveal.startup_still_building", return_value=True):
        return build_dashboard_window(app_config, **placed)


def test_minimize_routes_omniminimize_command(dashboard_window, dashboard_app_config):
    window = dashboard_window

    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    window.setWindowState(Qt.WindowState.WindowMinimized)

    assert cmd_file.read_text(encoding="utf-8").strip() == "omniminimize"


def test_omniminimize_not_routed_on_restore_or_repeat(dashboard_window, dashboard_app_config):
    window = dashboard_window
    window.setWindowState(Qt.WindowState.WindowMinimized)

    cmd_file = dashboard_app_config.dashboard_cmd_file
    cmd_file.unlink()

    window.setWindowState(Qt.WindowState.WindowMinimized)
    assert not cmd_file.exists()

    window.setWindowState(Qt.WindowState.WindowNoState)
    assert "omniminimize" not in cmd_file.read_text(encoding="utf-8")


def test_a_minimize_while_the_loading_screen_is_up_routes_nothing(dashboard_app_config):
    window = _window_under_the_loading_screen(dashboard_app_config)
    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    window.setWindowState(Qt.WindowState.WindowMinimized)

    assert not cmd_file.exists()
    window.close()


def test_the_first_restore_after_the_reveal_rearms_the_routing(dashboard_app_config):
    window = _window_under_the_loading_screen(dashboard_app_config)
    assert window._reveal.routing_suppressed is True
    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    window.setWindowState(Qt.WindowState.WindowMinimized)
    window.setWindowState(Qt.WindowState.WindowNoState)
    assert not cmd_file.exists()

    window.setWindowState(Qt.WindowState.WindowMinimized)
    cmd_file.unlink()
    window.setWindowState(Qt.WindowState.WindowNoState)
    assert cmd_file.read_text(encoding="utf-8").strip() == "omnirestore"
    window.close()


def test_do_render_leaves_a_window_still_hidden_for_loading_alone(dashboard_app_config):
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    window = _window_under_the_loading_screen(dashboard_app_config, launch_geometry=launch_geo)

    try:
        with patch("fun_time.dashboard_app.apply_dashboard_window_geometry") as apply_geo:
            window._do_render(_snapshot(), frozenset())
        apply_geo.assert_not_called()
    finally:
        window.close()


def test_restore_routes_omnirestore_command(dashboard_window, dashboard_app_config):
    window = dashboard_window
    window.setWindowState(Qt.WindowState.WindowMinimized)

    cmd_file = dashboard_app_config.dashboard_cmd_file
    cmd_file.unlink()

    window.setWindowState(Qt.WindowState.WindowNoState)
    assert cmd_file.read_text(encoding="utf-8").strip() == "omnirestore"

    cmd_file.unlink()
    window.setWindowState(Qt.WindowState.WindowNoState)
    assert not cmd_file.exists()
    window.setWindowState(Qt.WindowState.WindowMinimized)
    assert "omnirestore" not in cmd_file.read_text(encoding="utf-8")


def test_do_render_skips_geometry_reapply_while_minimized(dashboard_window, dashboard_app_config):
    window = dashboard_window

    with patch("fun_time.dashboard_app.apply_dashboard_window_geometry") as mock_geo, \
         patch.object(window, "isMinimized", return_value=True):
        window._do_render(None, frozenset())
    mock_geo.assert_not_called()

    with patch("fun_time.dashboard_app.apply_dashboard_window_geometry") as mock_geo, \
         patch.object(window, "isMinimized", return_value=False):
        window._do_render(None, frozenset())
    mock_geo.assert_called_once()


def test_dashboard_stays_hidden_during_loading(dashboard_app_config):
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    show_window = MagicMock()
    with patch("fun_time.loading_reveal.startup_still_building", return_value=True), \
         patch.object(ctypes.windll.user32, "ShowWindow", show_window):
        window = build_dashboard_window(dashboard_app_config, launch_geometry=launch_geo)

    try:
        assert window._reveal.deferred is True
        assert not window.isVisible()
        SW_HIDE, SW_SHOWMINNOACTIVE = 0, 7
        modes = [c.args[1] for c in show_window.call_args_list if c.args[0] == window._dash_hwnd]
        assert SW_HIDE in modes
        assert SW_SHOWMINNOACTIVE not in modes
    finally:
        window.close()


def test_dashboard_reveals_with_show_after_loading(dashboard_app_config):
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    with patch("fun_time.loading_reveal.startup_still_building", return_value=True):
        window = build_dashboard_window(dashboard_app_config, launch_geometry=launch_geo)

    try:
        assert window._reveal.deferred is True
        assert window._reveal.routing_suppressed is True

        show_window = MagicMock()
        with patch("fun_time.loading_reveal.startup_still_building", return_value=False), \
             patch.object(ctypes.windll.user32, "ShowWindow", show_window), \
             patch.object(window, "show") as mock_show:
            window._reveal.maybe_reveal()

        assert window._reveal.deferred is False
        assert window._reveal.routing_suppressed is False
        mock_show.assert_called_once()
        SW_SHOW = 5
        modes = [c.args[1] for c in show_window.call_args_list if c.args[0] == window._dash_hwnd]
        assert SW_SHOW in modes
    finally:
        window.close()


def test_dashboard_reveals_itself_underneath_the_loading_screen(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)

    with patch("fun_time.loading_reveal.startup_still_building", return_value=True):
        window = build_dashboard_window(
            app_config, launch_geometry=DashboardLaunchGeometry(100, 200, 300, 400))

    try:
        LOADING_SCREEN_HWND = 4242
        set_window_pos = MagicMock()
        with (
            patch("fun_time.loading_reveal.startup_still_building", return_value=False),
            patch("fun_time.loading_reveal.find_window_by_title", return_value=LOADING_SCREEN_HWND),
            patch.object(ctypes.windll.user32, "ShowWindow", MagicMock()),
            patch.object(ctypes.windll.user32, "SetWindowPos", set_window_pos),
            patch.object(window, "show"),
        ):
            window._reveal.maybe_reveal()

        placed = [c for c in set_window_pos.call_args_list
                  if c.args[0] == window._dash_hwnd]
        assert placed, "the reveal never placed the dashboard in the z-order"
        insert_after, flags = placed[-1].args[1], placed[-1].args[6]
        assert isinstance(insert_after, ctypes.c_void_p), (
            "a bare int is passed as c_int, which truncates a 64-bit HWND"
        )
        assert insert_after.value == LOADING_SCREEN_HWND, (
            "the panel was not inserted below the loading screen, so it lands on top of it"
        )
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        assert not flags & SWP_NOZORDER, "NOZORDER would discard the placement"
        assert flags & SWP_NOACTIVATE, "the reveal must not take focus"
    finally:
        window.close()


def test_dashboard_leaves_the_band_alone_when_there_is_no_loading_screen(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)

    with patch("fun_time.loading_reveal.startup_still_building", return_value=True):
        window = build_dashboard_window(
            app_config, launch_geometry=DashboardLaunchGeometry(100, 200, 300, 400))

    try:
        set_window_pos = MagicMock()
        with (
            patch("fun_time.loading_reveal.startup_still_building", return_value=False),
            patch("fun_time.loading_reveal.find_window_by_title", return_value=0),
            patch.object(ctypes.windll.user32, "ShowWindow", MagicMock()),
            patch.object(ctypes.windll.user32, "SetWindowPos", set_window_pos),
            patch.object(window, "show"),
        ):
            window._reveal.maybe_reveal()

        assert not [c for c in set_window_pos.call_args_list
                    if c.args[0] == window._dash_hwnd]
    finally:
        window.close()


def test_the_panel_leaves_the_topmost_band_before_it_becomes_visible(dashboard_app_config):
    with patch("fun_time.loading_reveal.startup_still_building", return_value=True):
        window = build_dashboard_window(dashboard_app_config)
    try:
        order: list[str] = []
        topmost = [True]

        def _band(hwnd, on_top, *, under=0):
            topmost[0] = on_top
            order.append(f"band={on_top} under={under}")

        def _seen(step):
            order.append(f"{step} topmost={topmost[0]}")

        with (
            patch("fun_time.loading_reveal.startup_still_building", return_value=False),
            patch("fun_time.loading_reveal.find_window_by_title",
                  side_effect=lambda *_a, **_k: (order.append("find"), 4242)[1]),
            patch("fun_time.loading_reveal.set_always_on_top", side_effect=_band),
            patch("fun_time.loading_reveal.show_own_window",
                  side_effect=lambda *_a: _seen("show_own")),
            patch.object(window, "show", side_effect=lambda: _seen("show")),
        ):
            window._reveal.maybe_reveal()

        assert order == [
            "find",
            "band=False under=0",
            "show topmost=False",
            "show_own topmost=False",
            "band=True under=4242",
        ]
    finally:
        window.close()


@pytest.mark.parametrize("building", [True, False])
def test_the_messages_start_held_exactly_when_the_panel_starts_hidden(
        building, dashboard_app_config):
    with patch("fun_time.loading_reveal.startup_still_building", return_value=building), \
         patch("fun_time.window_layout.enumerate_monitors", return_value=_monitors()):
        window = build_dashboard_window(dashboard_app_config)
    try:
        assert window._reveal.routing_suppressed is building
        window._messages.overlay = _FakeOverlay()
        _write_event(dashboard_app_config, "Clip saved", level=MESSAGE)
        with patch("fun_time.message_feed.loading_screen_is_up", return_value=True):
            window._messages.poll()

        assert (window._messages.overlay.flashed == []) is building
    finally:
        window.close()


def test_the_reveal_does_not_release_the_notices(dashboard_app_config):
    with patch("fun_time.loading_reveal.startup_still_building", return_value=True), \
         patch("fun_time.window_layout.enumerate_monitors", return_value=_monitors()):
        window = build_dashboard_window(dashboard_app_config)
    try:
        with (
            patch("fun_time.loading_reveal.startup_still_building", return_value=False),
            patch("fun_time.loading_reveal.find_window_by_title", return_value=0),
            patch("fun_time.loading_reveal.show_own_window", MagicMock()),
            patch("fun_time.loading_reveal.set_always_on_top", MagicMock()),
            patch.object(window, "show"),
        ):
            window._reveal.maybe_reveal()

        assert window._reveal.deferred is False
        window._messages.overlay = _FakeOverlay()
        _write_event(dashboard_app_config, "Clip saved", level=MESSAGE)
        with patch("fun_time.message_feed.loading_screen_is_up", return_value=True):
            window._messages.poll()
        assert window._messages.overlay.flashed == []
    finally:
        window.close()


def test_dashboard_syncs_own_topmost_with_omnipause(dashboard_window, dashboard_app_config):
    window = dashboard_window

    with patch("fun_time.win32.is_window_topmost", return_value=True), \
         patch("fun_time.win32.set_always_on_top") as mock_set:
        window._sync_own_topmost(omni_paused=True)
    mock_set.assert_called_once_with(window._dash_hwnd, False)

    with patch("fun_time.win32.is_window_topmost", return_value=False), \
         patch("fun_time.win32.set_always_on_top") as mock_set:
        window._sync_own_topmost(omni_paused=False)
    mock_set.assert_called_once_with(window._dash_hwnd, True)

    with patch("fun_time.win32.is_window_topmost", return_value=False), \
         patch("fun_time.win32.set_always_on_top") as mock_set:
        window._sync_own_topmost(omni_paused=True)
    mock_set.assert_not_called()


def test_every_render_drives_the_dashboards_band_off_the_snapshot(dashboard_window, dashboard_app_config):
    window = dashboard_window

    with patch.object(window, "_sync_own_topmost") as mock_sync:
        window._do_render(_snapshot(omni_paused=True), frozenset())
    mock_sync.assert_called_once_with(True)


def test_reference_dialog_syncs_topmost_with_omnipause():
    dialog = ReferenceDialog()
    try:
        hwnd = int(dialog.winId())

        with patch("fun_time.win32.is_window_topmost", return_value=True), \
             patch("fun_time.win32.set_always_on_top") as mock_set:
            dialog.sync_topmost(omni_paused=True)
        mock_set.assert_called_once_with(hwnd, False)

        with patch("fun_time.win32.is_window_topmost", return_value=False), \
             patch("fun_time.win32.set_always_on_top") as mock_set:
            dialog.sync_topmost(omni_paused=False)
        mock_set.assert_called_once_with(hwnd, True)

        with patch("fun_time.win32.is_window_topmost", return_value=False), \
             patch("fun_time.win32.set_always_on_top") as mock_set:
            dialog.sync_topmost(omni_paused=True)
        mock_set.assert_not_called()
    finally:
        dialog.close()


def test_the_popup_asks_for_its_handle_again_every_time_it_is_banded():
    dialog = ReferenceDialog()
    try:
        handles: list[int] = []
        with (
            patch.object(type(dialog), "winId", side_effect=[111, 222]),
            patch("fun_time.win32.is_window_topmost", return_value=False),
            patch("fun_time.win32.set_always_on_top",
                  side_effect=lambda hwnd, _on: handles.append(hwnd)),
        ):
            dialog.sync_topmost(omni_paused=False)
            dialog.sync_topmost(omni_paused=False)

        assert handles == [111, 222]
    finally:
        dialog.close()


def test_every_render_drives_the_popups_band_off_the_same_snapshot(dashboard_window, dashboard_app_config):
    window = dashboard_window

    with patch.object(window._reference, "sync_topmost") as mock_sync:
        window._do_render(_snapshot(omni_paused=True), frozenset())
    mock_sync.assert_called_once_with(True)


def test_sync_reference_topmost_is_a_noop_with_no_popup(dashboard_window, dashboard_app_config):
    window = dashboard_window

    assert window._reference.dialog is None
    with patch("fun_time.win32.set_always_on_top") as mock_set:
        window._reference.sync_topmost(omni_paused=True)
    mock_set.assert_not_called()


def test_opening_the_reference_under_omnipause_lands_it_non_topmost(dashboard_window, dashboard_app_config):
    window = dashboard_window

    window._last_snapshot = _snapshot(omni_paused=True)
    dialog = MagicMock()
    with patch("fun_time.dashboard_app.ReferenceDialog", return_value=dialog):
        window._on_action("help_reference")
    dialog.sync_topmost.assert_called_once_with(True)


def test_help_action_opens_dialog_locally_without_routing_command(dashboard_window, dashboard_app_config):
    window = dashboard_window

    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    with patch("fun_time.dashboard_app.ReferenceDialog", MagicMock()) as mock_dialog:
        window._on_action("help_reference")

    mock_dialog.assert_called_once()
    mock_dialog.return_value.show.assert_called_once()
    assert not cmd_file.exists(), "help_reference should not be written as a command"


def _pump_until(holds, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not holds() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def _fill_of(window, action: str):
    rect = dict(window._widget._scene.actions)[action]
    return next(item.fill for item in window._widget._scene.rects if item.rect == rect)


def test_a_pressed_controls_light_goes_out_by_itself(dashboard_window):
    resting = _fill_of(dashboard_window, VOICE_TOGGLE)

    dashboard_window._on_action(VOICE_TOGGLE)
    assert _fill_of(dashboard_window, VOICE_TOGGLE) != resting

    _pump_until(lambda: _fill_of(dashboard_window, VOICE_TOGGLE) == resting)
    assert _fill_of(dashboard_window, VOICE_TOGGLE) == resting


def test_a_closed_window_is_not_drawn_again_when_its_last_press_goes_out(dashboard_app_config):
    window = build_dashboard_window(dashboard_app_config)
    window._on_action(VOICE_TOGGLE)
    window.close()
    drawn_last = window._widget._scene

    deadline = time.monotonic() + 2 * PRESS_FLASH_S
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)

    assert window._widget._scene is drawn_last


def test_help_reference_press_toggles_reference_dialog(dashboard_window, dashboard_app_config):
    window = dashboard_window

    window._apply_presses(["help_reference"])

    try:
        assert window._reference.dialog is not None
        assert window._reference.dialog.isVisible()
    finally:
        if window._reference.dialog is not None:
            window._reference.dialog.close()


def test_help_reference_close_press_closes_reference_dialog(dashboard_window, dashboard_app_config):
    window = dashboard_window

    window._apply_presses(["help_reference"])
    assert window._reference.dialog is not None
    assert window._reference.dialog.isVisible()

    window._apply_presses(["help_reference_close"])

    assert window._reference.dialog is None or not window._reference.dialog.isVisible()

    window._apply_presses(["help_reference_close"])

    assert window._reference.dialog is None or not window._reference.dialog.isVisible()


def test_the_question_mark_is_lit_exactly_while_the_reference_is_open(dashboard_window):
    help_button = compute_dashboard_bar_layout().help_button

    def edge():
        return next(item.outline for item in dashboard_window._widget._scene.rects
                    if item.rect == help_button)

    dashboard_window._on_action("help_reference")
    try:
        assert edge() == BLUE
    finally:
        dashboard_window._reference.dialog.close()

    assert edge() == TEXT_MUTED


def test_opening_the_reference_find_pauses_the_app_so_typing_reaches_it(
        dashboard_window, dashboard_app_config):
    commands = dashboard_app_config.dashboard_cmd_file
    commands.unlink(missing_ok=True)
    dashboard_window._on_action("help_reference")
    try:
        dashboard_window._reference.dialog.open_find()

        assert commands.read_text(encoding="utf-8").split() == ["enter_omnipause"]
    finally:
        dashboard_window._reference.dialog.close()


def test_the_reference_takes_the_keyboard_however_it_was_opened(dashboard_window):
    # Opened by voice, the dashboard's process is one Windows will not hand the
    # foreground to, and Ctrl+F would go to whatever window the reference covers.
    with patch("fun_time.dashboard_app.bring_to_front") as bring:
        dashboard_window._apply_presses(["help_reference"])
    try:
        bring.assert_called_once_with(dashboard_window._reference.dialog)
    finally:
        dashboard_window._reference.dialog.close()


def test_toggle_reference_dialog_opens_then_closes(dashboard_window, dashboard_app_config):
    window = dashboard_window

    with patch("fun_time.dashboard_app.ReferenceDialog", MagicMock()) as mock_dialog:
        dialog = mock_dialog.return_value
        dialog.isVisible.return_value = False
        window._reference.toggle(omni_paused=False)
        dialog.show.assert_called_once()
        dialog.close.assert_not_called()

        dialog.isVisible.return_value = True
        window._reference.toggle(omni_paused=False)
        dialog.close.assert_called_once()


def test_reference_dialog_frame_fills_rfb_rect(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    rfb_rect = Rect(7, 408, 640, 984)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo, rfb_rect=rfb_rect)

    try:
        dialog = MagicMock()
        dialog.geometry.return_value = QRect(7, 408, 640, 984)
        dialog.frameGeometry.return_value = QRect(7 - 8, 408 - 31, 640 + 16, 984 + 39)
        with patch("fun_time.dashboard_app.ReferenceDialog", return_value=dialog):
            window._reference.open(omni_paused=False)
        calls = [c.args for c in dialog.setGeometry.call_args_list]
        assert calls[0] == (7, 408, 640, 984), "first placed at the rect"
        assert calls[-1] == (15, 439, 624, 945)
    finally:
        window.close()


def test_the_popup_is_fitted_to_the_browsers_rect_on_the_first_open_only(cfg_path: Path):
    config = load_config(cfg_path)
    app_config = load_dashboard_app_config(write_windows_bridge_manifest(config))
    rfb = Rect(x=10, y=20, width=300, height=400)
    window = build_dashboard_window(app_config, rfb_rect=rfb)
    try:
        with patch.object(window._reference, "_fit_to", wraps=window._reference._fit_to) as fit:
            window._reference.toggle(omni_paused=False)
            window._reference.toggle(omni_paused=False)
            window._reference.toggle(omni_paused=False)

        assert fit.call_count == 1
    finally:
        window._reference.close()
        window.close()


def test_the_popup_lands_in_the_right_band_after_it_is_shown_not_before(cfg_path: Path):
    config = load_config(cfg_path)
    app_config = load_dashboard_app_config(write_windows_bridge_manifest(config))
    window = build_dashboard_window(app_config)
    order: list[str] = []
    try:
        with patch("fun_time.dashboard_app.ReferenceDialog") as dialog_class:
            dialog = dialog_class.return_value
            dialog.isVisible.return_value = False
            dialog.show.side_effect = lambda: order.append("show")
            dialog.sync_topmost.side_effect = lambda _paused: order.append("band")
            window._reference.toggle(omni_paused=False)

        assert order.index("show") < order.index("band")
    finally:
        window.close()


def test_reference_dialog_window_title_is_the_content_title():
    dialog = ReferenceDialog()
    try:
        assert dialog.windowTitle() == "Hotkeys & Voice Commands Reference"
    finally:
        dialog.close()


def test_reference_dialog_renders_hotkeys_and_voice():
    dialog = ReferenceDialog()
    try:
        browser = dialog.findChild(QTextBrowser)
        assert browser is not None
        text = browser.toPlainText()
        assert "Esc" in text
        assert "genau" in text
        assert "Genau" in text
    finally:
        dialog.close()


def test_ctrl_f_in_the_reference_opens_a_find_over_its_text():
    dialog = ReferenceDialog()
    try:
        dialog.show()
        dialog.activateWindow()
        assert QTest.qWaitForWindowActive(dialog)

        QTest.keyClick(dialog, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        dialog.findChild(QLineEdit).setText("Ctrl+Alt+Q")

        assert dialog.findChild(QLineEdit).isVisible()
        assert dialog.findChild(QLabel, "findCount").text().startswith("1 of ")
        assert dialog.findChild(QTextBrowser).extraSelections()
    finally:
        dialog.close()


def test_the_reference_find_keeps_off_the_windows_edges():
    dialog = ReferenceDialog()
    try:
        dialog.resize(640, 480)
        dialog.show()
        dialog.open_find()
        QApplication.processEvents()

        strip = dialog.findChild(FindBar)
        held = strip.childrenRect().translated(strip.mapTo(dialog, QPoint(0, 0)))
        assert held.left() >= MARGIN_STANDARD
        assert dialog.width() - held.right() - 1 >= MARGIN_STANDARD
        assert dialog.height() - held.y() - held.height() >= MARGIN_STANDARD
    finally:
        dialog.close()


def test_lighten_color_adds_to_each_channel():
    result = lighten_color(QColor(0x2A, 0x30, 0x38), 50)
    assert (result.red(), result.green(), result.blue()) == (0x5C, 0x62, 0x6A)
    result2 = lighten_color(QColor(0, 0, 0), 30)
    assert (result2.red(), result2.green(), result2.blue()) == (30, 30, 30)


def test_lighten_color_caps_at_255():
    result = lighten_color(QColor(240, 240, 240), 50)
    assert (result.red(), result.green(), result.blue()) == (255, 255, 255)


def test_dashboard_widget_emits_action_on_click():
    layout = compute_dashboard_bar_layout()
    scene = build_dashboard_scene(layout, BarState(), icons=IconCache())

    widget = DashboardWidget(scene)
    received: list[str] = []
    widget.action_triggered.connect(received.append)

    quit_rect = None
    for action_id, rect in scene.actions:
        if action_id == QUIT_BUTTON:
            quit_rect = rect
            break
    assert quit_rect is not None
    event = MagicMock()
    event.position.return_value = QPoint(
        quit_rect.x + quit_rect.width // 2,
        quit_rect.y + quit_rect.height // 2,
    ).toPointF()
    widget.mousePressEvent(event)

    assert received == [QUIT_BUTTON]


def test_dashboard_widget_ignores_click_outside_actions():
    layout = compute_dashboard_bar_layout()
    scene = build_dashboard_scene(layout, BarState(), icons=IconCache())

    widget = DashboardWidget(scene)
    received: list[str] = []
    widget.action_triggered.connect(received.append)

    event = MagicMock()
    event.position.return_value = QPoint(0, 0).toPointF()
    widget.mousePressEvent(event)

    assert received == []


def _point_at(widget, x: int, y: int) -> None:
    QApplication.sendEvent(widget, QMouseEvent(
        QEvent.Type.MouseMove, QPointF(x, y), widget.mapToGlobal(QPointF(x, y)),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _leave(widget) -> None:
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))


def _center(rect) -> tuple[int, int]:
    return rect.x + rect.width // 2, rect.y + rect.height // 2


def _pixel_on_the_controls_ground(image, rect) -> QColor:
    return image.pixelColor(rect.x + BUTTON_ICON_INSET // 2, rect.y + rect.height // 2)


def _colors_in(image, rect) -> set[int]:
    return {image.pixel(x, y)
            for x in range(rect.x, rect.x + rect.width)
            for y in range(rect.y, rect.y + rect.height)}


class _Tooltip:
    def __init__(self) -> None:
        self.said: list[str | None] = []

    def showText(self, _at, text, _widget) -> None:
        self.said.append(text)

    def hideText(self) -> None:
        self.said.append(None)


@pytest.fixture
def bar():
    return DashboardWidget(_scene(), tooltip=_Tooltip())


def test_each_control_is_painted_on_the_ground_its_scene_gives_it(bar):
    image = bar.grab().toImage()

    assert [_pixel_on_the_controls_ground(image, item.rect) for item in bar._scene.rects] == [
        item.fill for item in bar._scene.rects]


def test_each_mark_and_the_apps_name_and_icon_are_painted_on_the_bar(bar):
    image = bar.grab().toImage()
    layout = compute_dashboard_bar_layout()

    for rect in (*(item.rect for item in bar._scene.images), layout.app_title, layout.app_icon):
        assert len(_colors_in(image, rect)) > 1


def test_the_control_under_the_pointer_is_painted_one_step_lighter(bar):
    item = bar._scene.rects[0]

    _point_at(bar, *_center(item.rect))

    assert _pixel_on_the_controls_ground(bar.grab().toImage(), item.rect) == hovered(item.fill)


def test_a_control_goes_back_to_its_ground_when_the_pointer_leaves_the_bar(bar):
    item = bar._scene.rects[0]
    _point_at(bar, *_center(item.rect))

    _leave(bar)

    assert _pixel_on_the_controls_ground(bar.grab().toImage(), item.rect) == item.fill


def test_a_control_under_the_pointer_names_itself(bar):
    rect = dict(bar._scene.actions)[QUIT_BUTTON]

    _point_at(bar, *_center(rect))

    assert bar._tooltip.said == [dict(bar._scene.hover_texts)[rect]]
    assert "Quit" in bar._tooltip.said[0]


def test_the_name_goes_away_when_the_pointer_is_off_every_control(bar):
    _point_at(bar, *_center(bar._scene.rects[0].rect))

    _point_at(bar, 0, 0)

    assert bar._tooltip.said[-1] is None


def test_a_control_that_takes_no_press_names_itself_but_does_not_light_under_the_pointer(bar):
    bar.set_scene(_scene(_snapshot(nothing_to_reset=True)))
    rect = compute_dashboard_bar_layout().reset_all_button
    ground = next(item.fill for item in bar._scene.rects if item.rect == rect)

    _point_at(bar, *_center(rect))

    assert "Reset" in bar._tooltip.said[0]
    assert _pixel_on_the_controls_ground(bar.grab().toImage(), rect) == ground


def test_every_control_on_the_bar_wears_a_drawn_mark():
    layout = compute_dashboard_bar_layout()
    scene = _scene()
    drawn = {item.rect: item.pixmap for item in scene.images}

    for rect, name in ((layout.quit_button, "power"),
                       (layout.help_button, "question"),
                       (layout.voice_panel, "mic")):
        satellite = _icon_side(rect)
        assert drawn[rect].toImage() == icon_pixmap(name, satellite, TEXT_PRIMARY).toImage(), name

    assert [item.text for item in scene.texts] == ["Fun Time"]


def test_the_help_mark_is_as_big_as_the_marks_beside_it():
    layout = compute_dashboard_bar_layout()
    drawn = {item.rect: item.pixmap for item in _scene().images}

    help_side = drawn[layout.help_button].width()
    mic_side = drawn[layout.voice_panel].width()
    assert abs(help_side - mic_side) <= 2


def test_the_pause_tooltip_names_the_act_the_press_will_take():
    layout = compute_dashboard_bar_layout()

    def tip(paused: bool) -> str:
        scene = _scene(_snapshot(omni_paused=paused))
        return next(text for rect, text in scene.hover_texts
                    if rect == layout.omnipause_button)

    assert tip(False) == "Pause everything"
    assert tip(True) == OMNIPAUSE_RESUME_TOOLTIP


def test_the_bar_wears_the_huds_button_edge_and_corner():
    layout = compute_dashboard_bar_layout()
    lit = {item.rect: item.outline
           for item in _scene(_snapshot(voice_active=True, f_mode=True)).rects}

    assert all(item.outline == TEXT_MUTED for item in _scene(_snapshot(omni_paused=True, voice_active=False)).rects)
    assert lit[layout.voice_panel] == BLUE
    assert lit[layout.fmode_button] == GREEN
    assert lit[layout.quit_button] == TEXT_MUTED
    assert _BUTTON_RADIUS == BUTTON_RADIUS_HUD


def test_a_pressed_control_keeps_the_edge_its_state_gave_it():
    layout = compute_dashboard_bar_layout()
    pressed = _scene(_snapshot(voice_active=True),
                     pressed_actions=frozenset({VOICE_TOGGLE}))

    assert next(item.outline for item in pressed.rects
                if item.rect == layout.voice_panel) == BLUE


def _icon_side(rect) -> int:
    return min(rect.width, rect.height) - 2 * BUTTON_ICON_INSET


class TestMarkCache:
    def test_the_same_mark_at_the_same_size_is_drawn_once(self):
        icons = IconCache()
        rect = compute_dashboard_bar_layout().quit_button

        assert icons.family_icon("power", rect) is icons.family_icon("power", rect)

    def test_a_control_of_another_size_gets_its_own(self):
        icons = IconCache()
        layout = compute_dashboard_bar_layout()

        assert icons.family_icon("power", layout.quit_button) is not icons.family_icon(
            "power", _resized(layout.quit_button, layout.quit_button.width // 2))

    def test_the_icon_is_rescaled_once_per_height(self):
        icons = IconCache()


        assert icons.icon(PROJECT_ICON, 24) is icons.icon(PROJECT_ICON, 24)
        assert icons.icon(PROJECT_ICON, 24) is not icons.icon(PROJECT_ICON, 25)

    def test_a_second_bar_paints_out_of_a_cache_of_its_own(self):
        rect = compute_dashboard_bar_layout().quit_button

        assert IconCache().family_icon("power", rect) is not IconCache().family_icon("power", rect)

    def test_a_mark_is_inset_from_its_button_as_far_as_a_huds_is(self):
        rect = compute_dashboard_bar_layout().quit_button

        assert IconCache().family_icon("power", rect).width() == _icon_side(rect)


def _resized(rect, satellite: int):
    return type(rect)(x=rect.x, y=rect.y, width=satellite, height=satellite)


def _press_port_file(app_config) -> Path:
    return app_config.state_dir / "dashboard_press_port.txt"


def _send_press(port: int, payload: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(payload, ("127.0.0.1", port))


def _drain(window, expected: str, *, timeout: float = 5.0) -> list[str]:
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen.extend(window._press_channel.take_all())
        if expected in seen:
            break
        time.sleep(0.02)
    return seen


def test_the_press_port_is_published_where_the_dispatch_loop_looks(
        dashboard_window, dashboard_app_config):
    published = _press_port_file(dashboard_app_config)

    raw = published.read_bytes()

    assert raw == raw.strip(), "the far end int()s the text it reads"
    assert int(raw.decode("utf-8")) == dashboard_window._press_channel.port


def test_a_datagram_becomes_a_press_on_the_queue(dashboard_window, dashboard_app_config):
    port = int(_press_port_file(dashboard_app_config).read_text(encoding="utf-8"))

    _send_press(port, b"help_reference")

    assert "help_reference" in _drain(dashboard_window, "help_reference")


def test_a_datagram_lights_the_control_it_names(dashboard_window, dashboard_app_config):
    resting = _fill_of(dashboard_window, VOICE_TOGGLE)
    port = int(_press_port_file(dashboard_app_config).read_text(encoding="utf-8"))

    _send_press(port, VOICE_TOGGLE.encode("utf-8"))

    _pump_until(lambda: _fill_of(dashboard_window, VOICE_TOGGLE) != resting)
    assert _fill_of(dashboard_window, VOICE_TOGGLE) != resting


def test_a_press_arrives_stripped_of_whatever_framed_it(
        dashboard_window, dashboard_app_config):
    port = int(_press_port_file(dashboard_app_config).read_text(encoding="utf-8"))

    _send_press(port, b"  quit \n")

    assert "quit" in _drain(dashboard_window, "quit")


def test_the_machine_picks_the_port_so_two_sessions_never_collide(
        dashboard_app_config, dashboard_window):
    other = build_dashboard_window(dashboard_app_config)
    try:
        assert dashboard_window._press_channel.port != other._press_channel.port
    finally:
        other.close()


def test_closing_the_window_asks_the_hotkey_script_to_end_the_session(dashboard_app_config):
    window = build_dashboard_window(dashboard_app_config)

    window.close()

    ahk_cmd_file = dashboard_app_config.state_dir / "ahk_cmd.txt"
    assert ahk_cmd_file.read_text(encoding="utf-8") == "end_session"


def test_a_window_that_cannot_ask_the_hotkey_script_still_closes(dashboard_app_config):
    window = build_dashboard_window(dashboard_app_config)
    (dashboard_app_config.state_dir / "ahk_cmd.txt").mkdir()

    assert window.close() is True
    assert not window._press_channel.listening


def test_closing_the_window_ends_the_listener(dashboard_app_config):
    window = build_dashboard_window(dashboard_app_config)
    listener = next(t for t in threading.enumerate() if t.name == "press-listener")

    window.close()

    assert not window._press_channel.listening
    listener.join(timeout=5.0)
    assert not listener.is_alive()


def test_closing_the_window_stops_reading_the_osr2_part(dashboard_app_config):
    window = build_dashboard_window(dashboard_app_config)

    window.close()

    assert not window._osr2._timer.isActive()


def test_the_dashboard_runs_on_an_application_made_where_it_is_let_go_of_safely(
        cfg_path, monkeypatch):
    made = []

    @contextmanager
    def application(argv):
        made.append(argv)
        yield MagicMock(exec=MagicMock(return_value=7))

    monkeypatch.setattr(dashboard_app, "application", application)
    monkeypatch.setattr(dashboard_app, "set_up_logging", lambda: None)
    monkeypatch.setattr(dashboard_app, "set_app_user_model_id", lambda _identity: None)
    monkeypatch.setattr(dashboard_app, "build_dashboard_window", lambda *_args, **_kwargs: None)
    dressed = []
    monkeypatch.setattr(dashboard_app, "dress_application", dressed.append)
    manifest = write_windows_bridge_manifest(load_config(cfg_path))

    assert dashboard_app.main([str(manifest)]) == 7
    assert made == [[]]
    assert len(dressed) == 1


def test_a_taskbar_that_refuses_the_sessions_identity_does_not_stop_the_dashboard():
    def refused(_identity):
        raise OSError("refused")

    dashboard_app.claim_taskbar_identity("FunTime.MadeUp", refused)


class _FakeOverlay:
    def __init__(self) -> None:
        self.flashed: list[tuple[str, object]] = []
        self.shut_down = False

    def flash(self, record, target) -> None:
        self.flashed.append((record.message, target))

    def shutdown(self) -> None:
        self.shut_down = True


def _monitors(primary=(0, 0, 1920, 1080), secondary=(1920, 0, 1080, 1920)):
    return [MonitorInfo(*primary), MonitorInfo(*secondary)]


def test_the_funestra_rects_come_from_the_layout_startup_positions_with(
        dashboard_app_config):
    with patch("fun_time.window_layout.enumerate_monitors", return_value=_monitors()):
        window = build_dashboard_window(dashboard_app_config)
    try:
        rects = window._messages.funestra_rects
    finally:
        window.close()

    layout = dashboard_app_config.layout

    primary, secondary = get_logical_monitor_rects(
        _monitors(), primary_index=layout.primary_monitor,
        secondary_index=layout.secondary_monitor)
    plan = compute_window_layout(
        primary_monitor=primary, secondary_monitor=secondary, layout_config=layout)
    main = compute_main_media_rect(secondary_monitor=secondary, layout_config=layout)

    assert (rects.portrait.x, rects.portrait.width) == (plan.portrait.x, plan.portrait.width)
    assert (rects.landscape.y, rects.landscape.height) == (plan.landscape.y, plan.landscape.height)
    assert (rects.dash.x, rects.dash.height) == (plan.dashboard.x, plan.dashboard.height)
    assert (rects.main.x, rects.main.width) == (main.x, main.width)


@pytest.mark.parametrize("failure", [ValueError("no such monitor"), OSError("no display")])
def test_monitors_that_cannot_be_read_leave_the_messages_off_rather_than_crash(
        failure, dashboard_app_config):
    with patch("fun_time.window_layout.enumerate_monitors", side_effect=failure):
        window = build_dashboard_window(dashboard_app_config)
    try:
        assert window._messages.funestra_rects is None
        assert window._messages.overlay is None
        window._messages.poll()
    finally:
        window.close()


def _message_window(dashboard_app_config, *, held: bool):
    with patch("fun_time.loading_reveal.startup_still_building", return_value=held), \
         patch("fun_time.window_layout.enumerate_monitors", return_value=_monitors()):
        window = build_dashboard_window(dashboard_app_config)
    window._messages.overlay = _FakeOverlay()
    return window


def _write_event(app_config, message: str, *, level: int) -> None:
    path = app_config.state_dir / EVENT_LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"ts": 1.0, "level": level, "source": SOURCE_DASH, "msg": message}) + "\n")


def test_a_main_funestra_message_lands_on_the_main_funestra_wherever_the_crown_has_put_it(
        dashboard_app_config):
    write_shared_state(shared_state_path(dashboard_app_config.state_dir),
                       BridgeState(majority=Crown.MAIN))
    window = _message_window(dashboard_app_config, held=False)
    try:
        path = dashboard_app_config.state_dir / EVENT_LOG_FILENAME
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"ts": 1.0, "level": MESSAGE, "source": "main", "msg": "Clip saved"}) + "\n")

        window._messages.poll()

        (_message, target), = window._messages.overlay.flashed
    finally:
        window.close()
    layout = dashboard_app_config.layout
    _primary, secondary = get_logical_monitor_rects(
        _monitors(), primary_index=layout.primary_monitor,
        secondary_index=layout.secondary_monitor)
    assert target == secondary_monitor_rects(secondary, layout, majority=Crown.MAIN).main


def test_nothing_flashes_through_the_loading_screen_and_nothing_is_dropped_either(
        dashboard_app_config):
    window = _message_window(dashboard_app_config, held=True)
    try:
        _write_event(dashboard_app_config, "Clip saved", level=MESSAGE)

        with patch("fun_time.message_feed.loading_screen_is_up", return_value=True):
            window._messages.poll()
        assert window._messages.overlay.flashed == []
        assert window._messages.offset == 0

        with patch("fun_time.message_feed.loading_screen_is_up", return_value=False):
            window._messages.poll()
        assert [m for m, _ in window._messages.overlay.flashed] == ["Clip saved"]
        assert window._messages.offset > 0
    finally:
        window.close()


def test_the_hold_is_latched_so_the_steady_state_reads_no_file(dashboard_app_config):
    window = _message_window(dashboard_app_config, held=True)
    try:
        loading_screen = patch("fun_time.message_feed.loading_screen_is_up", return_value=False)
        with loading_screen as is_up:
            window._messages.poll()
            window._messages.poll()
            window._messages.poll()

        assert is_up.call_count == 1
    finally:
        window.close()


def test_a_window_that_never_waited_asks_about_the_loading_screen_at_all(dashboard_app_config):
    window = _message_window(dashboard_app_config, held=False)
    try:
        with patch("fun_time.message_feed.loading_screen_is_up") as is_up:
            window._messages.poll()

        is_up.assert_not_called()
    finally:
        window.close()


def test_an_event_that_is_not_an_announcement_is_read_past_not_flashed(
        dashboard_app_config):
    window = _message_window(dashboard_app_config, held=False)
    try:
        _write_event(dashboard_app_config, "just a log line", level=logging.INFO)

        window._messages.poll()

        assert window._messages.overlay.flashed == []
        assert window._messages.offset > 0
    finally:
        window.close()


def test_words_flashed_with_no_log_line_land_over_the_funestra_they_name(dashboard_app_config):
    window = _message_window(dashboard_app_config, held=False)
    try:
        flash_unlogged(dashboard_app_config.state_dir, "unrecognized voice command: alpha beta",
                       source="portrait", level=logging.WARNING)
        deadline = time.monotonic() + 5.0
        while not window._messages.overlay.flashed and time.monotonic() < deadline:
            window._messages.poll()
            time.sleep(0.02)

        [(message, target)] = window._messages.overlay.flashed
        assert message == "unrecognized voice command: alpha beta"
        assert target == window._messages.funestra_rects.portrait
    finally:
        window.close()


def test_speech_that_is_no_command_is_flashed_and_listed_in_its_words_and_saved_without_them(
        dashboard_app_config):
    state_dir = dashboard_app_config.state_dir
    window = _message_window(dashboard_app_config, held=False)
    voice_log = logging.getLogger("fun_time.voice_control")
    written = EventLogHandler(event_log_path(state_dir))
    voice_log.addHandler(written)
    try:
        VoiceController(
            cmd_file=dashboard_app_config.dashboard_cmd_file,
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        ).handle_heard(Heard(
            Recognition(unrecognized_text="put the kettle on"),
            spoken_at=1.0, peak=2000, audio=b"", candidates={}, words_formed=True))
        deadline = time.monotonic() + 5.0
        while not window._messages.overlay.flashed and time.monotonic() < deadline:
            window._messages.poll()
            time.sleep(0.02)
        window._log_widget._poll()
        listed = [window._log_widget._list.item(row).text()
                  for row in range(window._log_widget._list.count())]
        flashed = [message for message, _target in window._messages.overlay.flashed]
    finally:
        voice_log.removeHandler(written)
        window.close()

    assert flashed == ["unrecognized voice command: put the kettle on"]
    [line] = [line for line in listed if "unrecognized" in line]
    assert line.endswith("unrecognized voice command: put the kettle on")
    assert "kettle" not in event_log_path(state_dir).read_text(encoding="utf-8")


def test_the_dashboard_records_which_checkout_it_ran_from(tmp_path: Path):
    written = record_source_checkout(tmp_path)

    assert written.name == SOURCE_CHECKOUT_FILENAME
    assert written.read_text(encoding="utf-8").strip() == str(source_checkout())
    assert (source_checkout() / "fun_time" / "dashboard_app.py").exists()


def test_the_inbox_and_the_reveal_leave_telling_the_gui_thread_to_the_window():
    def loads_qt(module: str) -> bool:
        result = subprocess.run(
            [sys.executable, "-c",
             f"import sys, {module}; print(any(m.startswith('PyQt6') for m in sys.modules))"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            env=environment_with_this_checkouts_siblings(),
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip() == "True"

    assert not loads_qt("fun_time.loopback_inbox")
    assert not loads_qt("fun_time.loading_reveal")


def test_the_dashboards_config_names_the_one_directory_its_files_live_in(tmp_path: Path):
    manifest_path = tmp_path / "state" / "windows_bridge_manifest.ini"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        "[layout]\nprimary_monitor=0\nsecondary_monitor=1\n"
        "main_top_ratio=0.5\nlandscape_width_ratio=0.5\n"
        "[commands]\ndashboard_state_file=dashboard_state.ini\n",
        encoding="utf-8",
    )

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.state_dir == manifest_path.parent
    assert app_config.dashboard_state_file == manifest_path.parent / "dashboard_state.ini"
    assert app_config.dashboard_cmd_file == manifest_path.parent / "dashboard_cmd.txt"


def _strip_app_config(cfg_factory, tmp_path):
    origenerator = tmp_path / "origenerator"
    origenerator.mkdir(exist_ok=True)
    config = load_config(cfg_factory({"paths": {"origenerator_dir": str(origenerator)}}))
    return load_dashboard_app_config(
        write_windows_bridge_manifest(config, tmp_path / "hosting_manifest.ini"))


@pytest.fixture
def mode_selector_window(cfg_factory, tmp_path):
    window = build_dashboard_window(
        _strip_app_config(cfg_factory, tmp_path),
        launch_geometry=DashboardLaunchGeometry(x=100, y=200, width=600, height=400))
    try:
        yield window
    finally:
        window.close()


def _on_the_mode_selector(selector, label: str) -> tuple[int, int]:
    rect = next(rect for rect, button in selector.buttons.placed if button.icon == label)
    return rect[0] + rect[2] // 2, rect[1] + rect[3] // 2


def _press_the_strip(window, label: str) -> None:
    QTest.mouseClick(window._mode_selector, Qt.MouseButton.LeftButton, pos=QPoint(*_on_the_mode_selector(window._mode_selector, label)))


def test_only_a_session_hosting_origenerator_gives_its_dashboard_the_strip(
        cfg_factory, cfg_path, tmp_path):
    plain = load_dashboard_app_config(
        write_windows_bridge_manifest(load_config(cfg_path), tmp_path / "plain_manifest.ini"))

    assert _strip_app_config(cfg_factory, tmp_path).rfb_mode_selector is True
    assert plain.rfb_mode_selector is False


def test_the_bar_the_log_and_the_strip_stand_in_one_column_left_of_the_osr2_part(
        cfg_factory, tmp_path, dashboard_window):
    window = _placed_where_a_session_places_it(_strip_app_config(cfg_factory, tmp_path))
    try:
        bar, log, strip, osr2 = window._widget, window._log_widget, window._mode_selector, window._osr2

        assert _at(window, bar) == QPoint(0, 0)
        assert _at(window, log) == QPoint(0, bar.height())
        assert _at(window, strip) == QPoint(0, bar.height() + log.height())
        assert log.width() == strip.width() == _at(window, osr2).x()
        assert (strip.height(), strip.y() + strip.height()) == (RFB_MODE_SELECTOR_HEIGHT, window.height())
        assert dashboard_window._mode_selector is None
    finally:
        window.close()


def test_the_strip_lights_whatever_holds_the_browsers_place(mode_selector_window):
    mode_selector_window._do_render(_snapshot(rfb_mode=RfbMode.ORIGENERATOR, origenerator_ready=True),
                            frozenset())

    assert [(button.icon, button.lit, button.dim) for _rect, button in mode_selector_window._mode_selector.buttons.placed] == [
        ("Random Favs Browser", False, False), ("Origenerator", True, False)]


def test_a_press_on_the_mode_selector_asks_for_that_one_in_the_browsers_place(mode_selector_window, tmp_path):
    mode_selector_window._do_render(_snapshot(origenerator_ready=True), frozenset())

    _press_the_strip(mode_selector_window, "Origenerator")
    _press_the_strip(mode_selector_window, "Random Favs Browser")

    assert poll_dashboard_commands(mode_selector_window._app_config.dashboard_cmd_file) == [
        "origenerator_activate", "rfb_activate"]


def test_the_strips_origenerator_takes_no_press_while_that_app_is_starting(mode_selector_window):
    mode_selector_window._do_render(_snapshot(origenerator_ready=False), frozenset())

    _press_the_strip(mode_selector_window, "Origenerator")

    assert poll_dashboard_commands(mode_selector_window._app_config.dashboard_cmd_file) == []


@pytest.fixture
def selector():
    widget = RfbModeSelectorWidget(tooltip=_Tooltip())
    widget.resize(600, widget.height())
    widget.show_state(_snapshot(origenerator_ready=True))
    return widget


def test_a_button_on_the_mode_selector_lights_under_the_pointer_until_it_leaves(selector):
    resting = selector.grab().toImage()

    _point_at(selector, *_on_the_mode_selector(selector, "Origenerator"))

    assert selector.grab().toImage() != resting
    assert selector._tooltip.said == [None]

    _leave(selector)

    assert selector.grab().toImage() == resting


def test_origenerator_on_the_mode_selector_says_it_is_starting_until_the_pointer_moves_off(selector):
    selector.show_state(_snapshot(origenerator_ready=False))

    _point_at(selector, *_on_the_mode_selector(selector, "Origenerator"))
    _point_at(selector, 0, 0)

    assert selector._tooltip.said == [STILL_STARTING_TOOLTIP, None]


def test_a_press_on_the_osr2_part_is_posted_where_the_bars_presses_are(
        dashboard_window, dashboard_app_config):
    part = published(dashboard_app_config.state_dir, a_part())
    osr2 = dashboard_window._osr2
    osr2.poll()
    osr2.grab()

    QTest.mouseClick(osr2, Qt.MouseButton.LeftButton, pos=QPoint(*center_of(part, "broker_panel")))

    assert poll_dashboard_commands(dashboard_app_config.dashboard_cmd_file) == ["broker_panel"]


def test_the_osr2_part_stands_at_the_windows_right_end_from_its_top(placed_window):
    osr2 = placed_window._osr2

    assert _at(placed_window, osr2) == QPoint(placed_window.width() - osr2.width(), 0)


def test_a_dashboard_that_cannot_leave_its_note_about_the_checkout_still_runs(tmp_path):
    no_such_folder = tmp_path / "gone"

    assert record_source_checkout(no_such_folder) == no_such_folder / SOURCE_CHECKOUT_FILENAME
