from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtGui import QColor

from shared_ui.colors import BLUE

from fun_time.manifest import write_windows_bridge_manifest
from fun_time.dashboard_app import (
    COLOR_APP_TITLE,
    COLOR_PANEL,
    DashboardLaunchGeometry,
    MarkCache,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
    build_dashboard_window,
    lighten_color,
    load_dashboard_app_config,
    write_dashboard_command,
)
from fun_time.dashboard_actions import (
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    VOICE_TOGGLE,
)
from fun_time.dashboard_runtime import DashboardSnapshot, DashboardWindowSnapshot
from fun_time.dashboard_layout import compute_dashboard_bar_layout, dashboard_window_height
from fun_time import load_config

def _scene(snapshot: DashboardSnapshot | None = None, **kwargs):
    layout = compute_dashboard_bar_layout()
    kwargs.setdefault("marks", MarkCache())
    return build_dashboard_scene(layout, snapshot, width=layout.content_width, **kwargs)


def _snapshot(**overrides) -> DashboardSnapshot:
    base = dict(
        omni_paused=False,
        window=DashboardWindowSnapshot(x=0, y=0, width=0, height=0),
    )
    base.update(overrides)
    return DashboardSnapshot(**base)


@pytest.fixture()
def dashboard_app_config(cfg_path):
    """The dashboard's own config, loaded back off a written manifest exactly
    the way the real dashboard process loads it."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    return load_dashboard_app_config(manifest_path)


@pytest.fixture()
def dashboard_window(dashboard_app_config):
    """A built dashboard window that closes however the test ends.

    The per-test try/finally discipline, guaranteed instead of remembered —
    a forgotten finally used to leak a top-level Qt window into the shared
    session QApplication for the rest of the run.
    """
    window = build_dashboard_window(
        dashboard_app_config,
        launch_geometry=DashboardLaunchGeometry(x=100, y=200, width=300, height=400),
    )
    try:
        yield window
    finally:
        window.close()


def _fill(scene, rect):
    return next(item.fill for item in scene.rects if item.rect == rect)


# --- the control bar ---------------------------------------------------------
# Every player draws its own HUD, so what is left on the bar is the handful of
# controls that belong to no player.


def test_the_bar_carries_only_what_belongs_to_no_player():
    """Quit, pause everything, the reference popup, and the microphone.  Anything
    about a particular player — the broker and F-mode included — is on that
    player's HUD."""
    scene = _scene()

    assert [action for action, _rect in scene.actions] == [
        QUIT_BUTTON, OMNIPAUSE_TOGGLE, HELP_REFERENCE, VOICE_TOGGLE,
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

    from shared_ui.colors import TEXT_PRIMARY
    from shared_ui.icons import glyph_pixmap

    def mark(paused: bool):
        scene = _scene(_snapshot(omni_paused=paused))
        return next(i.pixmap for i in scene.images if i.rect == layout.omnipause_button)

    side = _mark_side(layout.omnipause_button)
    assert mark(False).toImage() == glyph_pixmap("pause", side, TEXT_PRIMARY).toImage()
    assert mark(True).toImage() == glyph_pixmap("play", side, TEXT_PRIMARY).toImage()


def test_pausing_everything_does_not_paint_the_button_a_state_color():
    """The chips beside it are lights; this is a button, and coloring it would
    read as one of them having come on."""
    layout = compute_dashboard_bar_layout()

    assert _fill(_scene(_snapshot(omni_paused=True)), layout.omnipause_button) == COLOR_PANEL
    assert _fill(_scene(), layout.quit_button) == COLOR_PANEL


def test_the_microphone_comes_on_with_what_it_reports():
    layout = compute_dashboard_bar_layout()

    assert _fill(_scene(_snapshot(voice_active=True)), layout.voice_panel) == BLUE
    assert _fill(_scene(_snapshot(voice_active=False)), layout.voice_panel) == COLOR_PANEL


def test_the_microphone_carries_its_own_mark():
    """It is an icon rather than a word, so the bar stays a bar."""
    layout = compute_dashboard_bar_layout()
    drawn = {item.rect for item in _scene().images}

    assert layout.voice_panel in drawn


def test_the_microphone_is_the_one_the_family_shares():
    """Origenerator's toolbar wears this same drawing.

    The two apps sit on one screen at once, and while each painted its own
    microphone the two marks came out visibly different shapes -- one control
    reading as two.  So the mark on the panel has to be the shared glyph itself,
    pixel for pixel, rather than a copy that can drift from it again.
    """
    from shared_ui.colors import TEXT_PRIMARY
    from shared_ui.icons import glyph_pixmap

    panel = compute_dashboard_bar_layout().voice_panel
    drawn = {item.rect: item.pixmap for item in _scene().images}

    expected = glyph_pixmap("mic", _mark_side(panel), TEXT_PRIMARY)
    assert drawn[panel].toImage() == expected.toImage()


def test_every_control_names_itself_on_hover():
    scene = _scene()

    assert {rect for rect, _text in scene.hover_texts} == {rect for _a, rect in scene.actions}
    assert all(text for _rect, text in scene.hover_texts)


def test_a_pressed_control_lightens_while_the_press_shows():
    """Onto the family's own on-ground, which is what Origenerator's toolbar
    lights a control with -- these buttons had their own lightening before, and
    sat on a darker resting ground than any other app's."""
    from shared_ui.colors import BG_BUTTON, BG_BUTTON_ACTIVE

    layout = compute_dashboard_bar_layout()

    resting = _fill(_scene(), layout.quit_button)
    pressed = _fill(_scene(pressed_actions=frozenset({QUIT_BUTTON})), layout.quit_button)

    assert resting == BG_BUTTON
    assert pressed == BG_BUTTON_ACTIVE


def test_a_control_wearing_a_state_color_lightens_that_color_instead():
    """A pressed voice panel has to stay blue.  Sending every press to the one
    gray ground would say "voice is off" for as long as the finger is down."""
    layout = compute_dashboard_bar_layout()

    pressed = _fill(_scene(_snapshot(voice_active=True),
                           pressed_actions=frozenset({VOICE_TOGGLE})),
                    layout.voice_panel)

    assert pressed == lighten_color(BLUE)


def test_the_config_reads_only_what_the_bar_needs(tmp_path: Path):
    """The dashboard used to read every player's status file to draw its boxes.
    It draws no boxes, so it reads none of them."""
    config = load_config(Path("fun_time_config.example.json"))
    manifest_path = write_windows_bridge_manifest(config, tmp_path / "manifest.ini")

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.layout.primary_monitor == config.layout.primary_monitor
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


def test_minimize_routes_omniminimize_command(dashboard_window, dashboard_app_config):
    """Minimizing the dashboard writes the omniminimize command for the dispatch loop."""

    window = dashboard_window

    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    window._maybe_route_omniminimize(now_minimized=True, was_minimized=False)

    assert cmd_file.read_text(encoding="utf-8").strip() == "omniminimize"


def test_omniminimize_not_routed_on_restore_or_repeat(dashboard_window, dashboard_app_config):
    """Only the not-minimized -> minimized transition routes; restore/repeat do not."""

    window = dashboard_window

    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    # Restore (minimized -> normal) must not route.
    window._maybe_route_omniminimize(now_minimized=False, was_minimized=True)
    # Already minimized, state re-asserted — no new transition.
    window._maybe_route_omniminimize(now_minimized=True, was_minimized=True)

    assert not cmd_file.exists()


def test_the_first_restore_after_the_reveal_rearms_the_routing(dashboard_window, dashboard_app_config):
    """The reveal from hidden fires no restore edge, so the FIRST edge after
    it is the reveal's own restore: it must not route omnirestore — but it
    must clear the suppression, or the dashboard silently stops routing the
    minimize gestures for the rest of the session."""
    window = dashboard_window
    window._suppress_minimize_routing = True
    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    window._maybe_route_omnirestore(now_minimized=False, was_minimized=True)
    assert not cmd_file.exists()  # the reveal's own edge routes nothing

    window._maybe_route_omnirestore(now_minimized=False, was_minimized=True)
    assert cmd_file.read_text(encoding="utf-8").strip() == "omnirestore"


def test_do_render_leaves_a_window_still_hidden_for_loading_alone(dashboard_app_config):
    """The deferred half of the geometry guard: while the window is hidden
    behind the loading cover, a render must not touch its geometry — the
    reveal owns the first placement.  (The minimized half has its own test
    below.)"""
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    with patch("fun_time.dashboard_app.startup_still_building", return_value=True):
        window = build_dashboard_window(dashboard_app_config, launch_geometry=launch_geo)

    try:
        with patch("fun_time.dashboard_app.apply_dashboard_window_geometry") as apply_geo:
            window._do_render(_snapshot(), frozenset())
        apply_geo.assert_not_called()
    finally:
        window.close()


def test_restore_routes_omnirestore_command(dashboard_window, dashboard_app_config):
    """Un-minimizing the dashboard writes omnirestore so the others come back too."""

    window = dashboard_window

    cmd_file = dashboard_app_config.dashboard_cmd_file
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


def test_do_render_skips_geometry_reapply_while_minimized(dashboard_window, dashboard_app_config):
    """The refresh loop must not re-assert geometry on a minimized window (which would restore it)."""

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
    """While startup is still building the room the dashboard is fully hidden
    (SW_HIDE) — never shown, never minimized — so there is no flash and no
    minimize animation.  Built by hand: the deferral is decided at
    construction, so the patches must wrap the build itself."""
    import ctypes
    from unittest.mock import MagicMock

    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    show_window = MagicMock()
    with patch("fun_time.dashboard_app.startup_still_building", return_value=True), \
         patch.object(ctypes.windll.user32, "ShowWindow", show_window):
        window = build_dashboard_window(dashboard_app_config, launch_geometry=launch_geo)

    try:
        assert window._deferred_for_loading is True
        assert not window.isVisible()
        SW_HIDE, SW_SHOWMINNOACTIVE = 0, 7
        modes = [c.args[1] for c in show_window.call_args_list if c.args[0] == window._dash_hwnd]
        assert SW_HIDE in modes
        assert SW_SHOWMINNOACTIVE not in modes
    finally:
        window.close()


def test_dashboard_reveals_with_show_after_loading(dashboard_app_config):
    """Once startup reaches its last phase the dashboard is shown (SW_SHOW) and
    minimize routing is re-enabled — a reveal from hidden fires no restore edge
    to do it.  The cover is still up at that point; the two tests below say
    where the panel is put relative to it.  Built by hand: the deferral is
    decided at construction, so the patch must wrap the build itself."""
    import ctypes
    from unittest.mock import MagicMock

    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)
    with patch("fun_time.dashboard_app.startup_still_building", return_value=True):
        window = build_dashboard_window(dashboard_app_config, launch_geometry=launch_geo)

    try:
        assert window._deferred_for_loading is True
        assert window._suppress_minimize_routing is True

        show_window = MagicMock()
        with patch("fun_time.dashboard_app.startup_still_building", return_value=False), \
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


def test_dashboard_reveals_itself_underneath_the_cover(cfg_path: Path):
    """The panel shows itself while the cover is still up, so it must go BELOW it.

    Both windows are topmost and showing a window puts it at the top of its band,
    so a plain reveal would paint the panel over the cover until the cover's next
    200ms poll re-asserted itself — a flash of exactly what the cover is there to
    prevent.  So the same SetWindowPos that shows it names the cover as the window
    to sit under, and does not activate.
    """
    import ctypes
    from unittest.mock import MagicMock

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)

    with patch("fun_time.dashboard_app.startup_still_building", return_value=True):
        window = build_dashboard_window(
            app_config, launch_geometry=DashboardLaunchGeometry(100, 200, 300, 400))

    try:
        COVER_HWND = 4242
        set_window_pos = MagicMock()
        with (
            patch("fun_time.dashboard_app.startup_still_building", return_value=False),
            patch("fun_time.dashboard_app.find_window_by_title", return_value=COVER_HWND),
            patch.object(ctypes.windll.user32, "ShowWindow", MagicMock()),
            patch.object(ctypes.windll.user32, "SetWindowPos", set_window_pos),
            patch.object(window, "show"),
        ):
            window._maybe_reveal_after_loading()

        placed = [c for c in set_window_pos.call_args_list
                  if c.args[0] == window._dash_hwnd]
        assert placed, "the reveal never placed the dashboard in the z-order"
        insert_after, flags = placed[-1].args[1], placed[-1].args[6]
        assert isinstance(insert_after, ctypes.c_void_p), (
            "a bare int is passed as c_int, which truncates a 64-bit HWND"
        )
        assert insert_after.value == COVER_HWND, (
            "the panel was not inserted below the cover, so it lands on top of it"
        )
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        assert not flags & SWP_NOZORDER, "NOZORDER would discard the placement"
        assert flags & SWP_NOACTIVATE, "the reveal must not take focus"
    finally:
        window.close()


def test_dashboard_leaves_the_z_order_alone_when_there_is_no_cover(cfg_path: Path):
    """No cover to find is not a reason to move the panel around: it is shown
    where it already sits, exactly as it was before there was a cover to duck."""
    import ctypes
    from unittest.mock import MagicMock

    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)

    with patch("fun_time.dashboard_app.startup_still_building", return_value=True):
        window = build_dashboard_window(
            app_config, launch_geometry=DashboardLaunchGeometry(100, 200, 300, 400))

    try:
        set_window_pos = MagicMock()
        with (
            patch("fun_time.dashboard_app.startup_still_building", return_value=False),
            patch("fun_time.dashboard_app.find_window_by_title", return_value=0),
            patch.object(ctypes.windll.user32, "ShowWindow", MagicMock()),
            patch.object(ctypes.windll.user32, "SetWindowPos", set_window_pos),
            patch.object(window, "show"),
        ):
            window._maybe_reveal_after_loading()

        placed = [c for c in set_window_pos.call_args_list
                  if c.args[0] == window._dash_hwnd]
        assert placed
        SWP_NOZORDER = 0x0004
        assert placed[-1].args[6] & SWP_NOZORDER
    finally:
        window.close()


def test_dashboard_syncs_own_topmost_with_omnipause(dashboard_window, dashboard_app_config):
    """OmniPause must free the desktop, so the dashboard drops its OWN topmost
    while paused (via its reliable handle, since the orchestrator's drop of this
    Qt window is unreliable) and restores it after — drift-corrected, so it
    never issues a redundant SetWindowPos."""

    window = dashboard_window

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


def test_every_render_drives_the_dashboards_band_off_the_snapshot(dashboard_window, dashboard_app_config):
    """Every render drives the topmost sync off the snapshot's omni_paused, so
    the dashboard's band stays correct even if Qt re-asserts its StaysOnTop."""

    window = dashboard_window

    with patch.object(window, "_sync_own_topmost") as mock_sync:
        window._do_render(_snapshot(omni_paused=True), frozenset())
    # True, from the snapshot: rendering with None and asserting False
    # was satisfied by an implementation that hardcodes False.
    mock_sync.assert_called_once_with(True)


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


def test_every_render_drives_the_popups_band_off_the_same_snapshot(dashboard_window, dashboard_app_config):
    """Every render drives the popup's band off the same snapshot the dashboard's
    own band comes from, so a pause entered while it is open drops it too."""

    window = dashboard_window

    with patch.object(window, "_sync_reference_topmost") as mock_sync:
        window._do_render(_snapshot(omni_paused=True), frozenset())
    mock_sync.assert_called_once_with(True)


def test_sync_reference_topmost_is_a_noop_with_no_popup(dashboard_window, dashboard_app_config):
    """The popup only exists once it has been opened; before that there is no
    window to band, and the sync must not reach for one."""

    window = dashboard_window

    assert window._reference_dialog is None
    with patch("fun_time.dashboard_app.set_always_on_top") as mock_set:
        window._sync_reference_topmost(omni_paused=True)
    mock_set.assert_not_called()


def test_opening_the_reference_under_omnipause_lands_it_non_topmost(dashboard_window, dashboard_app_config):
    """Qt applies StaysOnTop on show, so opening the popup mid-pause would
    strand it over the freed desktop until the next refresh — it is banded at
    open time instead, from the last snapshot's omni_paused."""
    from unittest.mock import MagicMock

    window = dashboard_window

    window._last_snapshot = _snapshot(omni_paused=True)
    dialog = MagicMock()
    with patch("fun_time.dashboard_app.ReferenceDialog", return_value=dialog):
        window._show_reference_dialog()
    dialog.sync_topmost.assert_called_once_with(True)


def test_help_action_opens_dialog_locally_without_routing_command(dashboard_window, dashboard_app_config):
    """Help is a pure UI concern — it opens a dialog and must not write a dispatch command."""
    from unittest.mock import MagicMock

    window = dashboard_window

    cmd_file = dashboard_app_config.dashboard_cmd_file
    if cmd_file.exists():
        cmd_file.unlink()

    with patch("fun_time.dashboard_app.ReferenceDialog", MagicMock()) as mock_dialog:
        window._on_action("help_reference")

    mock_dialog.assert_called_once()
    mock_dialog.return_value.show.assert_called_once()
    # Must NOT be routed to the dispatch loop / command file.
    assert not cmd_file.exists(), "help_reference should not be written as a command"


def test_help_reference_press_toggles_reference_dialog(dashboard_window, dashboard_app_config):
    """A voice "help" reaches the dashboard as a UDP press, not a button click;
    processing that press must toggle the reference popup."""

    window = dashboard_window

    window._apply_presses(["help_reference"])

    try:
        # The observable: a real popup is up.  (Asserting the toggle METHOD
        # was called passed even if the toggle itself did nothing.)
        assert window._reference_dialog is not None
        assert window._reference_dialog.isVisible()
    finally:
        if window._reference_dialog is not None:
            window._reference_dialog.close()


def test_help_reference_close_press_closes_reference_dialog(dashboard_window, dashboard_app_config):
    """A voice "close help" arrives as a press and must only dismiss the popup —
    never open it."""

    window = dashboard_window

    window._apply_presses(["help_reference"])  # a popup is open...
    assert window._reference_dialog is not None and window._reference_dialog.isVisible()

    window._apply_presses(["help_reference_close"])  # ...and the close dismisses it

    assert window._reference_dialog is None or not window._reference_dialog.isVisible()

    window._apply_presses(["help_reference_close"])  # closing again must not reopen

    assert window._reference_dialog is None or not window._reference_dialog.isVisible()


def test_toggle_reference_dialog_opens_then_closes(dashboard_window, dashboard_app_config):
    """The same trigger opens the popup, then closes it on the next invocation."""
    from unittest.mock import MagicMock

    window = dashboard_window

    with patch("fun_time.dashboard_app.ReferenceDialog", MagicMock()) as mock_dialog:
        dialog = mock_dialog.return_value
        dialog.isVisible.return_value = False
        window._toggle_reference_dialog()  # closed → opens
        dialog.show.assert_called_once()
        dialog.close.assert_not_called()

        dialog.isVisible.return_value = True
        window._toggle_reference_dialog()  # visible → closes
        dialog.close.assert_called_once()


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


def test_dashboard_widget_emits_action_on_click():
    """Clicking inside an action rect should emit action_triggered with the action ID."""
    from PyQt6.QtCore import QPoint
    from fun_time.dashboard_app import DashboardWidget

    layout = compute_dashboard_bar_layout()
    scene = build_dashboard_scene(layout, width=layout.content_width, marks=MarkCache())

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


def test_dashboard_widget_ignores_click_outside_actions():
    """Clicking outside any action rect should not emit."""
    from PyQt6.QtCore import QPoint
    from fun_time.dashboard_app import DashboardWidget

    layout = compute_dashboard_bar_layout()
    scene = build_dashboard_scene(layout, width=layout.content_width, marks=MarkCache())

    widget = DashboardWidget()
    widget.set_scene(scene)
    received: list[str] = []
    widget.action_triggered.connect(received.append)

    from unittest.mock import MagicMock
    event = MagicMock()
    event.position.return_value = QPoint(0, 0).toPointF()
    widget.mousePressEvent(event)

    assert received == []




def test_every_control_on_the_bar_wears_a_drawn_mark():
    """Typed as font characters, each control came out at whatever weight its
    face gave it -- the help "?" was set in the body face rather than a symbol
    one and was visibly smaller than every mark beside it, and quit's power
    symbol was a different weight from the one Evolver draws.  So the bar's
    controls are drawn now, and only the app's own name is set in type."""
    from shared_ui.colors import TEXT_PRIMARY
    from shared_ui.icons import glyph_pixmap

    layout = compute_dashboard_bar_layout()
    scene = _scene()
    drawn = {item.rect: item.pixmap for item in scene.images}

    for rect, name in ((layout.quit_button, "power"),
                       (layout.help_button, "question"),
                       (layout.voice_panel, "mic")):
        side = _mark_side(rect)
        assert drawn[rect].toImage() == glyph_pixmap(name, side, TEXT_PRIMARY).toImage(), name

    assert [item.text for item in scene.texts] == ["Fun Time"]


def test_the_help_mark_is_as_big_as_the_marks_beside_it():
    """It was a text character among icons and read as an afterthought."""
    layout = compute_dashboard_bar_layout()
    drawn = {item.rect: item.pixmap for item in _scene().images}

    help_side = drawn[layout.help_button].width()
    mic_side = drawn[layout.voice_panel].width()
    assert abs(help_side - mic_side) <= 2


def test_the_pause_tooltip_names_the_act_the_press_will_take():
    """The mark already flips to a play triangle when everything is paused; the
    tooltip said "Pause everything" either way, so hovering a paused bar offered
    to do what it had already done."""
    from fun_time.dashboard_app import OMNIPAUSE_RESUME_TOOLTIP

    layout = compute_dashboard_bar_layout()

    def tip(paused: bool) -> str:
        scene = _scene(_snapshot(omni_paused=paused))
        return next(text for rect, text in scene.hover_texts
                    if rect == layout.omnipause_button)

    assert tip(False) == "Pause everything"
    assert tip(True) == OMNIPAUSE_RESUME_TOOLTIP


def test_the_bar_wears_the_familys_button_edge_and_radius():
    """The scene's rects carry the family's subtle outline, and the corner
    radius the painter rounds with is shared_ui's — the cross-repo metric
    every app's buttons share, not a number of this module's own.  (The
    radius reaches pixels only inside paintEvent, so the shared constant is
    the closest drawn fact a scene-level test can pin.)"""
    from shared_ui.colors import BORDER_SUBTLE
    from shared_ui.spacing import BUTTON_RADIUS

    from fun_time.dashboard_app import _BUTTON_RADIUS

    rects = _scene().rects
    assert rects
    assert all(item.outline == BORDER_SUBTLE for item in rects)
    assert _BUTTON_RADIUS == BUTTON_RADIUS


def _mark_side(rect) -> int:
    """How big a mark on *rect* is drawn: the family's icon size, or the control
    itself when that is smaller.  Every button in every app hugs its mark by the
    same amount, which is what this number is."""
    from shared_ui.spacing import BUTTON_ICON

    return min(BUTTON_ICON, min(rect.width, rect.height))


class TestMarkCache:
    """The pixmaps the bar is painted from, and who owns them.

    Both were module-level dicts that grew for the life of the process and were
    never cleared: one keyed marks by (name, width, height) under an annotation
    that said (name, height), the other icons by (file, height).
    """

    def test_the_same_mark_at_the_same_size_is_drawn_once(self):
        marks = MarkCache()
        rect = compute_dashboard_bar_layout().quit_button

        assert marks.mark("power", rect) is marks.mark("power", rect)

    def test_a_control_of_another_size_gets_its_own(self):
        marks = MarkCache()
        layout = compute_dashboard_bar_layout()

        assert marks.mark("power", layout.quit_button) is not marks.mark(
            "power", _resized(layout.quit_button, layout.quit_button.width // 2))

    def test_the_icon_is_rescaled_once_per_height(self):
        marks = MarkCache()

        assert marks.icon("icon.ico", 24) is marks.icon("icon.ico", 24)
        assert marks.icon("icon.ico", 24) is not marks.icon("icon.ico", 25)

    def test_a_second_bar_paints_out_of_a_cache_of_its_own(self):
        """The point of the owner: nothing is shared between two bars, and a
        test starts with an empty one instead of whatever ran before it."""
        rect = compute_dashboard_bar_layout().quit_button

        assert MarkCache().mark("power", rect) is not MarkCache().mark("power", rect)

    def test_a_mark_is_drawn_at_the_familys_icon_size_not_the_controls(self):
        """Every button in every app hugs its mark by the same amount."""
        layout = compute_dashboard_bar_layout()
        rect = layout.quit_button

        assert MarkCache().mark("power", rect).width() == _mark_side(rect)


def _resized(rect, side: int):
    """*rect* with its own position and a square side, for the cache cases."""
    return type(rect)(x=rect.x, y=rect.y, width=side, height=side)


# --- the press channel -------------------------------------------------------
# The dispatch loop tells the bar that a hotkey or a voice phrase took by
# sending it the action id over UDP, so the control flashes the way a click on
# it would.  The loop finds the port in a file this end publishes.


def _press_port_file(app_config) -> Path:
    return app_config.dashboard_state_file.parent / "dashboard_press_port.txt"


def _send_press(port: int, payload: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(payload, ("127.0.0.1", port))


def _drain(window, expected: str, *, timeout: float = 5.0) -> list[str]:
    """Every action the channel queued, up to and including *expected*."""
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
    """The far end reads exactly this file and int()s exactly this text
    (windows_bridge_dispatch_loop._send_press); a trailing newline or another
    encoding would leave every press undeliverable and say nothing."""
    published = _press_port_file(dashboard_app_config)

    raw = published.read_bytes()

    assert raw == raw.strip(), "the far end int()s the text it reads"
    assert int(raw.decode("utf-8")) == dashboard_window._press_channel.port


def test_a_datagram_becomes_a_press_on_the_queue(dashboard_window, dashboard_app_config):
    port = int(_press_port_file(dashboard_app_config).read_text(encoding="utf-8"))

    _send_press(port, b"help_reference")

    assert "help_reference" in _drain(dashboard_window, "help_reference")


def test_a_press_arrives_stripped_of_whatever_framed_it(
        dashboard_window, dashboard_app_config):
    """The sender writes the bare verb with no terminator, but the reader
    strips anyway, so a sender that ever adds one stays understood."""
    port = int(_press_port_file(dashboard_app_config).read_text(encoding="utf-8"))

    _send_press(port, b"  quit \n")

    assert "quit" in _drain(dashboard_window, "quit")


def test_the_machine_picks_the_port_so_two_sessions_never_collide(
        dashboard_app_config, dashboard_window):
    """A fixed port would be taken by whichever session on this machine came
    up first, and every press of the second one would land in the first."""
    other = build_dashboard_window(dashboard_app_config)
    try:
        assert dashboard_window._press_channel.port != other._press_channel.port
    finally:
        other.close()


def test_closing_the_window_ends_the_listener(dashboard_app_config):
    """Several dashboards are built and closed in one test process; a listener
    left blocked in recvfrom would outlive every one of them."""
    window = build_dashboard_window(dashboard_app_config)
    listener = next(t for t in threading.enumerate() if t.name == "press-listener")

    window.close()

    assert not window._press_channel.listening
    listener.join(timeout=5.0)
    assert not listener.is_alive()


# --- the notice feed ---------------------------------------------------------
# A second, faster tail of the same event log the strip reads, flashing each
# announcement over the player it is about.


class _FakeOverlay:
    """A notice overlay that records what it was asked to flash."""

    def __init__(self) -> None:
        self.flashed: list[tuple[str, object]] = []
        self.shut_down = False

    def flash(self, record, target) -> None:
        self.flashed.append((record.message, target))

    def shutdown(self) -> None:
        self.shut_down = True


def _monitors(primary=(0, 0, 1920, 1080), secondary=(1920, 0, 1080, 1920)):
    """Two monitors, patched in where enumerate_monitors would read them."""
    from fun_time.monitors import MonitorInfo

    return [MonitorInfo(*primary), MonitorInfo(*secondary)]


def test_the_player_rects_come_from_the_layout_startup_positions_with(
        dashboard_app_config):
    """The toast has to land ON the window, not near it, so both ends compute
    the rect from the same two functions rather than from two descriptions."""
    from fun_time.window_layout import compute_main_media_rect, compute_window_layout

    with patch("fun_time.dashboard_app.enumerate_monitors", return_value=_monitors()):
        window = build_dashboard_window(dashboard_app_config)
    try:
        rects = window._player_rects
    finally:
        window.close()

    layout = dashboard_app_config.layout
    with patch("fun_time.dashboard_app.enumerate_monitors", return_value=_monitors()):
        from fun_time.monitors import get_logical_monitor_rects

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
def test_monitors_that_cannot_be_read_leave_the_notices_off_rather_than_crash(
        failure, dashboard_app_config):
    """A headless run has no monitors to enumerate; the panel still comes up,
    it just has nowhere to put a toast."""
    with patch("fun_time.dashboard_app.enumerate_monitors", side_effect=failure):
        window = build_dashboard_window(dashboard_app_config)
    try:
        assert window._player_rects is None
        assert window._notice_overlay is None
        window._poll_notices()  # and polling is a no-op rather than an error
    finally:
        window.close()


def _notice_window(dashboard_app_config, *, held: bool):
    """A window whose notice feed is wired to a fake overlay."""
    with patch("fun_time.dashboard_app.startup_still_building", return_value=held), \
         patch("fun_time.dashboard_app.enumerate_monitors", return_value=_monitors()):
        window = build_dashboard_window(dashboard_app_config)
    window._notice_overlay = _FakeOverlay()
    return window


def _write_event(app_config, message: str, *, level: int) -> None:
    """One line onto the shared event log, the way EventLogHandler writes it."""
    import json

    from fun_time.event_log import EVENT_LOG_FILENAME, SOURCE_DASH

    path = app_config.dashboard_state_file.parent / EVENT_LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"ts": 1.0, "level": level, "source": SOURCE_DASH, "msg": message}) + "\n")


def test_nothing_flashes_through_the_cover_and_nothing_is_dropped_either(
        dashboard_app_config):
    """A toast is topmost, so one that fired while the cover was up would
    appear for a moment through the scrim the cover is there to be.  Held, the
    read offset does not advance, so the announcement arrives afterwards over
    the room it is about."""
    window = _notice_window(dashboard_app_config, held=True)
    try:
        from fun_time.event_log import NOTICE

        _write_event(dashboard_app_config, "Clip saved", level=NOTICE)

        with patch("fun_time.dashboard_app.loading_cover_is_up", return_value=True):
            window._poll_notices()
        assert window._notice_overlay.flashed == []
        assert window._notice_offset == 0

        with patch("fun_time.dashboard_app.loading_cover_is_up", return_value=False):
            window._poll_notices()
        assert [m for m, _ in window._notice_overlay.flashed] == ["Clip saved"]
        assert window._notice_offset > 0
    finally:
        window.close()


def test_the_hold_is_latched_so_the_steady_state_reads_no_file(dashboard_app_config):
    """Once the cover is down it never comes back, and this runs four times a
    second for the length of the session."""
    window = _notice_window(dashboard_app_config, held=True)
    try:
        cover = patch("fun_time.dashboard_app.loading_cover_is_up", return_value=False)
        with cover as is_up:
            window._poll_notices()
            window._poll_notices()
            window._poll_notices()

        assert is_up.call_count == 1
    finally:
        window.close()


def test_a_window_that_never_waited_asks_about_the_cover_at_all(dashboard_app_config):
    """Started after the cover was gone, the feed is live from its first poll."""
    window = _notice_window(dashboard_app_config, held=False)
    try:
        with patch("fun_time.dashboard_app.loading_cover_is_up") as is_up:
            window._poll_notices()

        is_up.assert_not_called()
    finally:
        window.close()


def test_an_event_that_is_not_an_announcement_is_read_past_not_flashed(
        dashboard_app_config):
    """The strip shows every event; only the announcements get a toast — and an
    event that gets none must still not be re-read on the next poll."""
    import logging

    window = _notice_window(dashboard_app_config, held=False)
    try:
        _write_event(dashboard_app_config, "just a log line", level=logging.INFO)

        window._poll_notices()

        assert window._notice_overlay.flashed == []
        assert window._notice_offset > 0
    finally:
        window.close()


def test_the_dashboard_records_which_checkout_it_ran_from(tmp_path: Path):
    """A branch session runs from a worktree by setting the working directory,
    and nothing said whether that had taken.  A change that is in the code and
    not on the screen then leaves no way to tell an implementation fault from a
    delivery one, which costs a review round every time it happens."""
    from fun_time.dashboard_app import (
        SOURCE_CHECKOUT_FILENAME,
        record_source_checkout,
        source_checkout,
    )

    written = record_source_checkout(tmp_path)

    assert written.name == SOURCE_CHECKOUT_FILENAME
    assert written.read_text(encoding="utf-8").strip() == str(source_checkout())
    assert (source_checkout() / "fun_time" / "dashboard_app.py").exists()
