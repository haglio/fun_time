from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtGui import QColor

from shared_ui.colors import BLUE, GREEN

from fun_time.manifest import write_windows_bridge_manifest
from fun_time.dashboard_app import (
    COLOR_APP_TITLE,
    COLOR_PANEL,
    DashboardLaunchGeometry,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
    build_dashboard_window,
    lighten_color,
    load_dashboard_app_config,
    write_dashboard_command,
)
from fun_time.dashboard_actions import (
    FMODE_PANEL,
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    VOICE_TOGGLE,
)
from fun_time.dashboard_runtime import DashboardPanelSnapshot, DashboardSnapshot, DashboardWindowSnapshot
from fun_time.dashboard_layout import compute_dashboard_bar_layout, dashboard_window_height
from fun_time import load_config

def _scene(snapshot: DashboardSnapshot | None = None, **kwargs):
    layout = compute_dashboard_bar_layout()
    return build_dashboard_scene(layout, snapshot, width=layout.content_width, **kwargs)


def _snapshot(**overrides) -> DashboardSnapshot:
    base = dict(
        f_mode_enabled=False, primary_mode="nau", osr2_mode="controlled", omni_paused=False,
        primary=DashboardPanelSnapshot(path=""),
        portrait=DashboardPanelSnapshot(path=""),
        landscape=DashboardPanelSnapshot(path=""),
        window=DashboardWindowSnapshot(x=0, y=0, width=0, height=0),
    )
    base.update(overrides)
    return DashboardSnapshot(**base)


def _fill(scene, rect):
    return next(item.fill for item in scene.rects if item.rect == rect)


# --- the control bar ---------------------------------------------------------
# The dashboard drew a schematic of both monitors with a box per player, each
# carrying that player's buttons.  Every player draws its own HUD now, so what is
# left is the handful of controls that belong to no player.


def test_the_bar_carries_only_what_belongs_to_no_player():
    """Quit, pause everything, the reference popup, and the F-mode and voice
    lights.  Anything about a particular player — the broker included — is on that
    player's HUD."""
    scene = _scene()

    assert [action for action, _rect in scene.actions] == [
        QUIT_BUTTON, OMNIPAUSE_TOGGLE, HELP_REFERENCE, FMODE_PANEL, VOICE_TOGGLE,
    ]


def test_the_bar_is_only_as_wide_as_its_own_buttons():
    """It shares its row with the log's filter controls now, so it takes the width
    its buttons need and leaves the rest to them."""
    layout = compute_dashboard_bar_layout()

    assert _scene().width == layout.content_width
    assert _scene().height == layout.height


def test_the_window_is_the_bar_and_the_log_under_it():
    """The window used to be as tall as a scale drawing of the taller monitor,
    with the log squeezed into the strip beside it.  Now it is the bar plus the
    log — a fraction of the height, which the browser below inherits."""
    layout = compute_dashboard_bar_layout()

    assert dashboard_window_height() > layout.height
    assert dashboard_window_height() < 400


def test_the_app_names_itself_at_the_head_of_the_bar():
    scene = _scene()
    layout = compute_dashboard_bar_layout()

    title = next(item for item in scene.texts if item.text == "Fun Time")
    assert title.rect == layout.app_title
    assert title.color == COLOR_APP_TITLE
    assert any(item.rect == layout.app_icon for item in scene.images)


def test_the_pause_button_says_which_way_it_will_go():
    """Paused, it offers to resume; running, it offers to pause — the button is
    the action it will take, not the state it is in."""
    layout = compute_dashboard_bar_layout()

    def icon(paused: bool) -> str:
        scene = _scene(_snapshot(omni_paused=paused))
        return next(i.text for i in scene.texts if i.rect == layout.omnipause_button)

    assert icon(False) == "\u23F8"
    assert icon(True) == "\u25B6"


def test_pausing_everything_does_not_paint_the_button_a_state_colour():
    """The chips beside it are lights; this is a button, and colouring it would
    read as one of them having come on."""
    layout = compute_dashboard_bar_layout()

    assert _fill(_scene(_snapshot(omni_paused=True)), layout.omnipause_button) == COLOR_PANEL
    assert _fill(_scene(), layout.quit_button) == COLOR_PANEL


def test_the_f_mode_and_voice_lights_come_on_with_what_they_report():
    layout = compute_dashboard_bar_layout()

    assert _fill(_scene(_snapshot(f_mode_enabled=True)), layout.fmode_panel) == GREEN
    assert _fill(_scene(_snapshot()), layout.fmode_panel) == COLOR_PANEL
    assert _fill(_scene(_snapshot(voice_active=True)), layout.voice_panel) == BLUE
    assert _fill(_scene(_snapshot(voice_active=False)), layout.voice_panel) == COLOR_PANEL


def test_the_lights_carry_their_own_marks():
    """Each is an icon rather than a word, so the bar stays a bar."""
    layout = compute_dashboard_bar_layout()
    drawn = {item.rect for item in _scene().images}

    assert {layout.fmode_panel, layout.voice_panel} <= drawn


def test_every_control_names_itself_on_hover():
    scene = _scene()

    assert {rect for rect, _text in scene.hover_texts} == {rect for _a, rect in scene.actions}
    assert all(text for _rect, text in scene.hover_texts)


def test_a_pressed_control_lightens_while_the_press_shows():
    layout = compute_dashboard_bar_layout()

    resting = _fill(_scene(), layout.quit_button)
    pressed = _fill(_scene(pressed_actions=frozenset({QUIT_BUTTON})), layout.quit_button)

    assert pressed == lighten_color(resting)


def test_the_config_reads_only_what_the_bar_needs(tmp_path: Path):
    """The dashboard used to read every player's status file to draw its boxes.
    It draws no boxes, so it reads none of them."""
    config = load_config(Path("fun_time_config.example.json"))
    manifest_path = write_windows_bridge_manifest(config, tmp_path / "manifest.ini")

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.layout.main_monitor == config.layout.main_monitor
    assert app_config.dashboard_cmd_file.name == "dashboard_cmd.txt"
    assert not hasattr(app_config, "favs_file")


def test_write_dashboard_command_retries_past_a_transient_file_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The dispatch loop drains this file ~20x/s by renaming it, so a click whose
    write overlaps a drain hits a Windows sharing violation (WinError 32) — the
    same race AHK's QueueCommand retries past.  The write must retry rather than
    raise: unhandled, the PermissionError propagates out of the Qt slot and PyQt6
    aborts the whole dashboard, which is the "power button closed the Dash instead
    of quitting Fun Time" bug."""
    command_file = tmp_path / "state" / "dashboard_cmd.txt"
    command_file.parent.mkdir(parents=True)

    real_open = Path.open
    attempts = {"n": 0}

    def flaky_open(self: Path, *args: object, **kwargs: object):
        if self == command_file:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise PermissionError(32, "being used by another process")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)
    monkeypatch.setattr("player_core.file_channel.time.sleep", lambda _s: None)

    write_dashboard_command(command_file, "quit")  # must not raise

    assert attempts["n"] >= 2  # retried past the first failure
    assert command_file.read_text(encoding="utf-8").strip() == "quit"


def test_write_dashboard_command_drops_rather_than_raises_when_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the file stays locked for every retry, the click is dropped — never
    raised.  A raise here aborts the whole PyQt6 dashboard; a dropped click just
    means the user clicks again."""
    command_file = tmp_path / "state" / "dashboard_cmd.txt"
    command_file.parent.mkdir(parents=True)

    def always_locked(self: Path, *args: object, **kwargs: object):
        if self == command_file:
            raise PermissionError(32, "being used by another process")
        raise AssertionError("unexpected open")

    monkeypatch.setattr(Path, "open", always_locked)
    monkeypatch.setattr("player_core.file_channel.time.sleep", lambda _s: None)

    write_dashboard_command(command_file, "quit")  # must not raise

    assert not command_file.exists()


def test_write_dashboard_command_queues_rather_than_clobbers(tmp_path: Path):
    """Two clicks landing between dispatch-loop drains must both survive: the
    writer appends newline-terminated lines, so ``poll_dashboard_commands`` reads
    both in order rather than only the last."""
    from fun_time.windows_bridge_dispatch_loop import poll_dashboard_commands

    command_file = tmp_path / "state" / "dashboard_cmd.txt"

    write_dashboard_command(command_file, "portrait_lock")
    write_dashboard_command(command_file, "quit")

    assert poll_dashboard_commands(command_file) == ["portrait_lock", "quit"]


def test_dashboard_window_geometry_uses_snapshot_window_when_available():
    scene = _scene()
    snapshot = _snapshot(window=DashboardWindowSnapshot(111, 222, 333, 444))

    from PyQt6.QtWidgets import QWidget
    widget = QWidget()
    apply_dashboard_window_geometry(widget, snapshot, scene)
    geo = widget.geometry()

    assert (geo.x(), geo.y(), geo.width(), geo.height()) == (111, 222, 333, 444)


def test_dashboard_window_geometry_prefers_launch_geometry_when_provided():
    scene = _scene()

    from PyQt6.QtWidgets import QWidget
    widget = QWidget()
    apply_dashboard_window_geometry(
        widget,
        None,
        scene,
        launch_geometry=DashboardLaunchGeometry(x=11, y=22, width=333, height=444),
    )
    geo = widget.geometry()

    assert (geo.x(), geo.y(), geo.width(), geo.height()) == (11, 22, 333, 444)


def test_minimize_routes_omniminimize_command(cfg_path: Path):
    """Minimizing the dashboard writes the omniminimize command for the dispatch loop."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        cmd_file = app_config.dashboard_cmd_file
        if cmd_file.exists():
            cmd_file.unlink()

        window._maybe_route_omniminimize(now_minimized=True, was_minimized=False)

        assert cmd_file.read_text(encoding="utf-8").strip() == "omniminimize"
    finally:
        window.close()


def test_omniminimize_not_routed_on_restore_or_repeat(cfg_path: Path):
    """Only the not-minimized -> minimized transition routes; restore/repeat do not."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        cmd_file = app_config.dashboard_cmd_file
        if cmd_file.exists():
            cmd_file.unlink()

        # Restore (minimized -> normal) must not route.
        window._maybe_route_omniminimize(now_minimized=False, was_minimized=True)
        # Already minimized, state re-asserted — no new transition.
        window._maybe_route_omniminimize(now_minimized=True, was_minimized=True)

        assert not cmd_file.exists()
    finally:
        window.close()


def test_restore_routes_omnirestore_command(cfg_path: Path):
    """Un-minimizing the dashboard writes omnirestore so the others come back too."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        cmd_file = app_config.dashboard_cmd_file
        if cmd_file.exists():
            cmd_file.unlink()

        # Restore edge (minimized -> normal) routes omnirestore.
        window._maybe_route_omnirestore(now_minimized=False, was_minimized=True)
        assert cmd_file.read_text(encoding="utf-8").strip() == "omnirestore"

        # Minimize edge and steady state must not route omnirestore.
        cmd_file.unlink()
        window._maybe_route_omnirestore(now_minimized=True, was_minimized=False)
        window._maybe_route_omnirestore(now_minimized=False, was_minimized=False)
        assert not cmd_file.exists()
    finally:
        window.close()


def test_do_render_skips_geometry_reapply_while_minimized(cfg_path: Path):
    """The refresh loop must not re-assert geometry on a minimized window (which would restore it)."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        with patch("fun_time.dashboard_app.apply_dashboard_window_geometry") as mock_geo, \
             patch.object(window, "isMinimized", return_value=True):
            window._do_render(None, frozenset())
        mock_geo.assert_not_called()

        with patch("fun_time.dashboard_app.apply_dashboard_window_geometry") as mock_geo, \
             patch.object(window, "isMinimized", return_value=False):
            window._do_render(None, frozenset())
        mock_geo.assert_called_once()
    finally:
        window.close()


def test_dashboard_stays_hidden_during_loading(cfg_path: Path):
    """During the loading overlay the dashboard is fully hidden (SW_HIDE) — never
    shown, never minimized — so there is no flash and no minimize animation."""
    import ctypes
    from unittest.mock import MagicMock

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    show_window = MagicMock()
    with patch("fun_time.dashboard_app.loading_screen_active", return_value=True), \
         patch.object(ctypes.windll.user32, "ShowWindow", show_window):
        window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        assert window._deferred_for_loading is True
        assert not window.isVisible()
        SW_HIDE, SW_SHOWMINNOACTIVE = 0, 7
        modes = [c.args[1] for c in show_window.call_args_list if c.args[0] == window._dash_hwnd]
        assert SW_HIDE in modes
        assert SW_SHOWMINNOACTIVE not in modes
    finally:
        window.close()


def test_dashboard_reveals_with_show_after_loading(cfg_path: Path):
    """Once the overlay is gone the dashboard is shown (SW_SHOW) and minimize
    routing is re-enabled — a reveal from hidden fires no restore edge to do it."""
    import ctypes
    from unittest.mock import MagicMock

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    with patch("fun_time.dashboard_app.loading_screen_active", return_value=True):
        window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        assert window._deferred_for_loading is True
        assert window._suppress_minimize_routing is True

        show_window = MagicMock()
        with patch("fun_time.dashboard_app.loading_screen_active", return_value=False), \
             patch.object(ctypes.windll.user32, "ShowWindow", show_window), \
             patch.object(window, "show") as mock_show:
            window._maybe_reveal_after_loading()

        assert window._deferred_for_loading is False
        assert window._suppress_minimize_routing is False
        mock_show.assert_called_once()
        SW_SHOW = 5
        modes = [c.args[1] for c in show_window.call_args_list if c.args[0] == window._dash_hwnd]
        assert SW_SHOW in modes
    finally:
        window.close()


def test_dashboard_syncs_own_topmost_with_omnipause(cfg_path: Path):
    """OmniPause must free the desktop, so the dashboard drops its OWN topmost
    while paused (via its reliable handle, since the orchestrator's drop of this
    Qt window is unreliable) and restores it after — drift-corrected, so it
    never issues a redundant SetWindowPos."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        # Entering OmniPause while topmost drops the dashboard out of the band.
        with patch("fun_time.dashboard_app.is_window_topmost", return_value=True), \
             patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
            window._sync_own_topmost(omni_paused=True)
        mock_set.assert_called_once_with(window._dash_hwnd, False)

        # Leaving OmniPause while non-topmost floats it back on top.
        with patch("fun_time.dashboard_app.is_window_topmost", return_value=False), \
             patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
            window._sync_own_topmost(omni_paused=False)
        mock_set.assert_called_once_with(window._dash_hwnd, True)

        # Already in the desired band → no redundant SetWindowPos (no flicker).
        with patch("fun_time.dashboard_app.is_window_topmost", return_value=False), \
             patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
            window._sync_own_topmost(omni_paused=True)
        mock_set.assert_not_called()
    finally:
        window.close()


def test_do_render_syncs_own_topmost_from_snapshot(cfg_path: Path):
    """Every render drives the topmost sync off the snapshot's omni_paused, so
    the dashboard's band stays correct even if Qt re-asserts its StaysOnTop."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        with patch.object(window, "_sync_own_topmost") as mock_sync:
            window._do_render(None, frozenset())
        mock_sync.assert_called_once_with(False)
    finally:
        window.close()


def test_reference_dialog_syncs_topmost_with_omnipause():
    """The reference popup is its own top-level window — not a MANAGED_ROLE the
    orchestrator can drop, and not a child riding the dashboard's band — so it
    corrects its OWN band: out of topmost while paused, back on top after,
    drift-corrected so it never issues a redundant SetWindowPos."""
    from fun_time.dashboard_app import ReferenceDialog

    dialog = ReferenceDialog()
    try:
        hwnd = int(dialog.winId())

        # Entering OmniPause while topmost drops the popup out of the band.
        with patch("fun_time.dashboard_app.is_window_topmost", return_value=True), \
             patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
            dialog.sync_topmost(omni_paused=True)
        mock_set.assert_called_once_with(hwnd, False)

        # Leaving OmniPause while non-topmost floats it back on top.
        with patch("fun_time.dashboard_app.is_window_topmost", return_value=False), \
             patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
            dialog.sync_topmost(omni_paused=False)
        mock_set.assert_called_once_with(hwnd, True)

        # Already in the desired band → no redundant SetWindowPos (no flicker).
        with patch("fun_time.dashboard_app.is_window_topmost", return_value=False), \
             patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
            dialog.sync_topmost(omni_paused=True)
        mock_set.assert_not_called()
    finally:
        dialog.close()


def test_do_render_syncs_reference_topmost_from_snapshot(cfg_path: Path):
    """Every render drives the popup's band off the same snapshot the dashboard's
    own band comes from, so a pause entered while it is open drops it too."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        with patch.object(window, "_sync_reference_topmost") as mock_sync:
            window._do_render(_snapshot(omni_paused=True), frozenset())
        mock_sync.assert_called_once_with(True)
    finally:
        window.close()


def test_sync_reference_topmost_is_a_noop_with_no_popup(cfg_path: Path):
    """The popup only exists once it has been opened; before that there is no
    window to band, and the sync must not reach for one."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        assert window._reference_dialog is None
        with patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
            window._sync_reference_topmost(omni_paused=True)
        mock_set.assert_not_called()
    finally:
        window.close()


def test_opening_the_reference_under_omnipause_lands_it_non_topmost(cfg_path: Path):
    """Qt applies StaysOnTop on show, so opening the popup mid-pause would
    strand it over the freed desktop until the next refresh — it is banded at
    open time instead, from the last snapshot's omni_paused."""
    from unittest.mock import MagicMock

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        window._last_snapshot = _snapshot(omni_paused=True)
        dialog = MagicMock()
        with patch("fun_time.dashboard_app.ReferenceDialog", return_value=dialog):
            window._show_reference_dialog()
        dialog.sync_topmost.assert_called_once_with(True)
    finally:
        window.close()


def test_help_action_opens_dialog_locally_without_routing_command(cfg_path: Path):
    """Help is a pure UI concern — it opens a dialog and must not write a dispatch command."""
    from unittest.mock import MagicMock
    from fun_time.dashboard_app import DashboardWindow

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        cmd_file = app_config.dashboard_cmd_file
        if cmd_file.exists():
            cmd_file.unlink()

        with patch("fun_time.dashboard_app.ReferenceDialog", MagicMock()) as mock_dialog:
            window._on_action("help_reference")

        mock_dialog.assert_called_once()
        mock_dialog.return_value.show.assert_called_once()
        # Must NOT be routed to the dispatch loop / command file.
        assert not cmd_file.exists(), "help_reference should not be written as a command"
    finally:
        window.close()


def test_help_reference_press_toggles_reference_dialog(cfg_path: Path):
    """A voice "help" reaches the dashboard as a UDP press, not a button click;
    processing that press must toggle the reference popup."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        with patch.object(window, "_toggle_reference_dialog") as mock_toggle:
            window._press_queue.put("help_reference")
            window._handle_press_event()
        mock_toggle.assert_called_once()
    finally:
        window.close()


def test_help_reference_close_press_closes_reference_dialog(cfg_path: Path):
    """A voice "close help" arrives as a press and must only dismiss the popup —
    never open it."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        with patch.object(window, "_close_reference_dialog") as mock_close, \
             patch.object(window, "_toggle_reference_dialog") as mock_toggle:
            window._press_queue.put("help_reference_close")
            window._handle_press_event()
        mock_close.assert_called_once()
        mock_toggle.assert_not_called()
    finally:
        window.close()


def test_toggle_reference_dialog_opens_then_closes(cfg_path: Path):
    """The same trigger opens the popup, then closes it on the next invocation."""
    from unittest.mock import MagicMock

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        with patch("fun_time.dashboard_app.ReferenceDialog", MagicMock()) as mock_dialog:
            dialog = mock_dialog.return_value
            dialog.isVisible.return_value = False
            window._toggle_reference_dialog()  # closed → opens
            dialog.show.assert_called_once()
            dialog.close.assert_not_called()

            dialog.isVisible.return_value = True
            window._toggle_reference_dialog()  # visible → closes
            dialog.close.assert_called_once()
    finally:
        window.close()


def test_reference_dialog_frame_fills_rfb_rect(cfg_path: Path):
    """The reference popup is sized so its whole FRAME — title bar included —
    fills the RFB rect: it is placed at the rect, then its client insets by the
    window's chrome margins so the decoration no longer overhangs the top."""
    from unittest.mock import MagicMock
    from PyQt6.QtCore import QRect
    from fun_time.dashboard_layout import Rect

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    rfb_rect = Rect(7, 408, 640, 984)

    window = build_dashboard_window(app_config, launch_geometry=launch_geo, rfb_rect=rfb_rect)

    try:
        dialog = MagicMock()
        # An 8px border and a 31px title bar: the client is where it was placed,
        # the frame extends around it.
        dialog.geometry.return_value = QRect(7, 408, 640, 984)
        dialog.frameGeometry.return_value = QRect(7 - 8, 408 - 31, 640 + 16, 984 + 39)
        with patch("fun_time.dashboard_app.ReferenceDialog", return_value=dialog):
            window._show_reference_dialog()
        calls = [c.args for c in dialog.setGeometry.call_args_list]
        assert calls[0] == (7, 408, 640, 984), "first placed at the rect"
        # Then inset so the frame fills it: down by the title bar, in by the borders.
        assert calls[-1] == (15, 439, 624, 945)
    finally:
        window.close()


def test_reference_dialog_window_title_is_the_content_title():
    """The popup carries its name on the window chrome (the redundant in-window
    heading was removed), so the chrome title IS the reference's title."""
    from fun_time.dashboard_app import ReferenceDialog

    dialog = ReferenceDialog()
    try:
        assert dialog.windowTitle() == "Hotkeys & Voice Commands Reference"
    finally:
        dialog.close()


def test_reference_dialog_renders_hotkeys_and_voice():
    """The real dialog must render the reference content via QTextBrowser."""
    from PyQt6.QtWidgets import QTextBrowser
    from fun_time.dashboard_app import ReferenceDialog

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


def test_lighten_color_adds_to_each_channel():
    result = lighten_color(QColor(0x2A, 0x30, 0x38), 50)
    assert (result.red(), result.green(), result.blue()) == (0x5C, 0x62, 0x6A)
    result2 = lighten_color(QColor(0, 0, 0), 30)
    assert (result2.red(), result2.green(), result2.blue()) == (30, 30, 30)


def test_lighten_color_caps_at_255():
    result = lighten_color(QColor(240, 240, 240), 50)
    assert (result.red(), result.green(), result.blue()) == (255, 255, 255)


def _make_snapshot(*, primary_mode: str = "nau") -> DashboardSnapshot:
    return DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode=primary_mode,
        osr2_mode="auto",
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )


def _make_layout(_cfg_path: Path | None = None):
    return compute_dashboard_bar_layout()


def test_dashboard_widget_emits_action_on_click(cfg_path: Path):
    """Clicking inside an action rect should emit action_triggered with the action ID."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QApplication
    from fun_time.dashboard_app import DashboardWidget

    layout = _make_layout(cfg_path)
    scene = build_dashboard_scene(layout, width=layout.content_width)

    widget = DashboardWidget()
    widget.set_scene(scene)
    received: list[str] = []
    widget.action_triggered.connect(received.append)

    # Simulate a click in the center of the quit button
    from fun_time.dashboard_actions import QUIT_BUTTON
    quit_rect = None
    for action_id, rect in scene.actions:
        if action_id == QUIT_BUTTON:
            quit_rect = rect
            break
    assert quit_rect is not None
    from unittest.mock import MagicMock
    event = MagicMock()
    event.position.return_value = QPoint(
        quit_rect.x + quit_rect.width // 2,
        quit_rect.y + quit_rect.height // 2,
    ).toPointF()
    widget.mousePressEvent(event)

    assert received == [QUIT_BUTTON]


def test_dashboard_widget_ignores_click_outside_actions(cfg_path: Path):
    """Clicking outside any action rect should not emit."""
    from PyQt6.QtCore import QPoint
    from fun_time.dashboard_app import DashboardWidget

    layout = _make_layout(cfg_path)
    scene = build_dashboard_scene(layout, width=layout.content_width)

    widget = DashboardWidget()
    widget.set_scene(scene)
    received: list[str] = []
    widget.action_triggered.connect(received.append)

    from unittest.mock import MagicMock
    event = MagicMock()
    event.position.return_value = QPoint(0, 0).toPointF()
    widget.mousePressEvent(event)

    assert received == []


