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
        main_mode="nau", osr2_mode="controlled", omni_paused=False,
        main=DashboardPanelSnapshot(path=""),
        portrait=DashboardPanelSnapshot(path=""),
        landscape=DashboardPanelSnapshot(path=""),
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
# The dashboard drew a schematic of both monitors with a box per player, each
# carrying that player's buttons.  Every player draws its own HUD now, so what is
# left is the handful of controls that belong to no player.


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


def test_do_render_syncs_own_topmost_from_snapshot(dashboard_window, dashboard_app_config):
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


def test_do_render_syncs_reference_topmost_from_snapshot(dashboard_window, dashboard_app_config):
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
    from fun_time.dashboard_app import DashboardWindow

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

    with patch.object(window, "_toggle_reference_dialog") as mock_toggle:
        window._press_queue.put("help_reference")
        window._handle_press_event()
    mock_toggle.assert_called_once()


def test_help_reference_close_press_closes_reference_dialog(dashboard_window, dashboard_app_config):
    """A voice "close help" arrives as a press and must only dismiss the popup —
    never open it."""

    window = dashboard_window

    with patch.object(window, "_close_reference_dialog") as mock_close, \
         patch.object(window, "_toggle_reference_dialog") as mock_toggle:
        window._press_queue.put("help_reference_close")
        window._handle_press_event()
    mock_close.assert_called_once()
    mock_toggle.assert_not_called()


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


def _make_snapshot(*, main_mode: str = "nau") -> DashboardSnapshot:
    return DashboardSnapshot(
        main_mode=main_mode,
        osr2_mode="auto",
        omni_paused=False,
        main=DashboardPanelSnapshot("", False),
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




def test_every_control_on_the_bar_wears_a_drawn_mark(qtbot=None):
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
