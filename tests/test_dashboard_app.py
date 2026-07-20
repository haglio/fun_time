from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtGui import QColor

from fun_time.manifest import write_windows_bridge_manifest
from fun_time.dashboard_app import (
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_CABLE,
    COLOR_CABLE_DIM,
    COLOR_RED,
    COLOR_APP_TITLE,
    COLOR_PINK,
    COLOR_PANEL,
    COLOR_YELLOW,
    ICON_LOCK,
    ICON_TRASH,
    DashboardArcItem,
    DashboardLaunchGeometry,
    DashboardLineItem,
    DashboardOvalItem,
    PlayerHydration,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
    build_dashboard_window,
    hydrate_dashboard_snapshot,
    lighten_color,
    load_dashboard_app_config,
    poll_players,
    resolve_logical_monitor_sizes,
    write_dashboard_command,
)
from fun_time.dashboard_runtime import DashboardPanelSnapshot, DashboardSnapshot, DashboardWindowSnapshot, NauStatus
from fun_time.dashboard_layout import DashboardPreviewLayout, Size, compute_dashboard_preview_layout
from fun_time import load_config


@pytest.fixture(autouse=True)
def _silence_the_background_player_poller():
    """``build_dashboard_window`` starts a daemon thread that polls every player
    each refresh — Nau for the primary panel and each native satellite's status
    file.  Stub it so the background thread never reads real state files
    mid-test.  The tests that exercise ``poll_players`` call the function object
    they imported, so they still run the real one."""
    with patch("fun_time.dashboard_app.poll_players", return_value=PlayerHydration()):
        yield


def test_dashboard_app_loads_layout_from_manifest(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, destination=tmp_path / "windows_bridge_launch.ini")

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.layout.main_monitor == 1
    assert app_config.layout.secondary_monitor == 2
    assert app_config.layout.landscape_width_ratio == config.layout.landscape_width_ratio
    assert app_config.favs_file == config.paths.favs_file
    assert app_config.nau_status_file == config.paths.state_dir / "nau_status.txt"
    assert app_config.portrait_status_file == config.paths.state_dir / "portrait_status.txt"
    assert app_config.landscape_status_file == config.paths.state_dir / "landscape_status.txt"
    assert app_config.dashboard_state_file == config.paths.state_dir / "dashboard_state.ini"
    assert app_config.dashboard_cmd_file == config.paths.state_dir / "dashboard_cmd.txt"


def test_dashboard_highlights_primary_for_ai_video_with_funscript(cfg_path: Path):
    """Primary panel should light green for an AI video with funscript — no source roots needed."""
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    ai_root = config.paths.portrait_dirs[0]
    ai_root.mkdir(parents=True, exist_ok=True)
    ai_video = ai_root / "ai_clip.mp4"
    ai_video.write_text("video", encoding="utf-8")
    ai_funscript = Path(str(ai_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")) / "ai_clip.funscript"
    ai_funscript.parent.mkdir(parents=True, exist_ok=True)
    ai_funscript.write_text("script", encoding="utf-8")
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("100.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="manual",
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot(str(ai_video), False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(10, 20, 300, 200),
    )

    scene = build_dashboard_scene(
        preview_layout,
        snapshot,
        favs_file=config.paths.favs_file,
        broker_heartbeat_file=heartbeat_file,
    )

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[preview_layout.primary_panel] == COLOR_GREEN


def test_f_mode_does_not_force_panels_green_for_non_matching_videos(cfg_path: Path):
    """F-mode on must not paint panels green on faith. Each panel reflects the
    actual current video, so a non-funscript primary and non-favorite satellites
    stay neutral — surfacing anything that slipped past the filter instead of
    hiding it behind an unconditional green."""
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    favs_file = config.paths.favs_file
    favs_file.parent.mkdir(parents=True, exist_ok=True)
    favs_file.write_text("local_file,web_url\n", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=True,
        primary_mode="nau",
        osr2_mode="controlled",
        primary_responsive=True,
        omni_paused=False,
        primary=DashboardPanelSnapshot(r"C:\clips\no_funscript.mp4", False),
        portrait=DashboardPanelSnapshot(r"C:\clips\not_a_fav_p.mp4", False),
        landscape=DashboardPanelSnapshot(r"C:\clips\not_a_fav_l.mp4", False),
        window=DashboardWindowSnapshot(10, 20, 300, 200),
    )

    scene = build_dashboard_scene(preview_layout, snapshot, favs_file=favs_file)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[preview_layout.primary_panel] == COLOR_PANEL
    assert fills[preview_layout.portrait_panel] == COLOR_PANEL
    assert fills[preview_layout.landscape_panel] == COLOR_PANEL


def test_dashboard_app_resolves_landscape_monitor_as_logical_main_even_if_ids_are_swapped():
    main_monitor, secondary_monitor = resolve_logical_monitor_sizes(
        [Size(1440, 3440), Size(2560, 1392)],
        main_monitor_index=1,
        secondary_monitor_index=2,
    )

    assert main_monitor == Size(2560, 1392)
    assert secondary_monitor == Size(1440, 3440)


def test_dashboard_app_builds_scene_from_preview_layout(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    assert scene.width == preview_layout.dashboard_width
    assert scene.height == preview_layout.dashboard_height
    assert any(item.text == "Fun Time" for item in scene.texts)
    assert len(_cable_lines(scene)) == 1, "Default scene should show cable (one straight line)"


def test_dashboard_app_scene_uses_runtime_snapshot_when_available(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    primary_root = config.paths.nau_library_dirs[0]
    primary_root.mkdir(parents=True, exist_ok=True)
    primary_path = primary_root / "primary.mp4"
    primary_path.write_text("video", encoding="utf-8")
    primary_script = Path(str(primary_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")) / "primary.funscript"
    primary_script.parent.mkdir(parents=True, exist_ok=True)
    primary_script.write_text("script", encoding="utf-8")
    portrait_path = Path(r"C:\clips\portrait.mp4")
    landscape_path = Path(r"C:\clips\landscape.mp4")
    favs_file = config.paths.favs_file
    favs_file.parent.mkdir(parents=True, exist_ok=True)
    favs_file.write_text(
        f"local_file,web_url\n{portrait_path},\n",
        encoding="utf-8",
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("100.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="auto",
        primary_responsive=True,
        omni_paused=False,
        primary=DashboardPanelSnapshot(str(primary_path), False),
        portrait=DashboardPanelSnapshot(str(portrait_path), True),
        landscape=DashboardPanelSnapshot(str(landscape_path), False),
        window=DashboardWindowSnapshot(10, 20, 300, 200),
    )

    scene = build_dashboard_scene(
        preview_layout,
        snapshot,
        favs_file=favs_file,
        broker_heartbeat_file=heartbeat_file,
    )

    texts = {item.text for item in scene.texts}
    fills = {item.rect: item.fill for item in scene.rects}
    assert len(_cable_lines(scene)) == 1, "Cable should be one straight line"
    assert "Nau" in texts
    assert "Portrait\nAI Player" in texts
    assert not any(".mp4" in item.text for item in scene.texts)
    assert fills[preview_layout.primary_panel] == COLOR_PINK, "Auto mode makes primary pink"
    assert fills[preview_layout.portrait_panel] == COLOR_GREEN
    assert any(action == "portrait_lock" for action, _rect in scene.actions)
    assert any(action == "genau_activate" for action, _rect in scene.actions)


def test_dashboard_labels_hybrid_as_nau_plus_genau(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="hybrid",
        osr2_mode="off",
        primary_responsive=True,
        omni_paused=False,
        primary=DashboardPanelSnapshot(r"C:\clips\p.mp4", False),
        portrait=DashboardPanelSnapshot(r"C:\clips\portrait.mp4", False),
        landscape=DashboardPanelSnapshot(r"C:\clips\landscape.mp4", False),
        window=DashboardWindowSnapshot(10, 20, 300, 200),
    )

    scene = build_dashboard_scene(preview_layout, snapshot)

    assert "Hybrid Nau+Genau" in {item.text for item in scene.texts}


def test_osr2_auto_mode_uses_pink_not_green(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("100.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="auto",
        primary_responsive=True,
        omni_paused=False,
        primary=DashboardPanelSnapshot("C:\\clips\\primary.mp4", False),
        portrait=DashboardPanelSnapshot("C:\\clips\\portrait.mp4", False),
        landscape=DashboardPanelSnapshot("C:\\clips\\landscape.mp4", False),
        window=DashboardWindowSnapshot(10, 20, 300, 200),
    )

    scene = build_dashboard_scene(
        preview_layout,
        snapshot,
        broker_heartbeat_file=heartbeat_file,
    )

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[preview_layout.osr2_panel] == COLOR_PINK
    assert COLOR_PINK != COLOR_GREEN


def test_osr2_non_auto_uses_panel_color(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="controlled",
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("C:\\clips\\primary.mp4", False),
        portrait=DashboardPanelSnapshot("C:\\clips\\portrait.mp4", False),
        landscape=DashboardPanelSnapshot("C:\\clips\\landscape.mp4", False),
        window=DashboardWindowSnapshot(10, 20, 300, 200),
    )

    scene = build_dashboard_scene(preview_layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[preview_layout.osr2_panel] == COLOR_PANEL


def test_quarter_button_uses_neutral_grey(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.quarter_button] == COLOR_PANEL


def test_genau_panel_is_pink_when_active(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.primary_panel] == COLOR_PINK


def test_dashboard_app_writes_commands_for_click_actions(tmp_path: Path):
    command_file = tmp_path / "state" / "dashboard_cmd.txt"

    write_dashboard_command(command_file, "portrait_next")

    assert command_file.read_text(encoding="utf-8").strip() == "portrait_next"


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


def test_dashboard_window_geometry_uses_snapshot_window_when_available(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    scene = build_dashboard_scene(preview_layout)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="controlled",
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(111, 222, 333, 444),
    )

    from PyQt6.QtWidgets import QWidget
    widget = QWidget()
    apply_dashboard_window_geometry(widget, snapshot, scene)
    geo = widget.geometry()

    assert (geo.x(), geo.y(), geo.width(), geo.height()) == (111, 222, 333, 444)


def test_dashboard_window_geometry_prefers_launch_geometry_when_provided(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    scene = build_dashboard_scene(preview_layout)

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


def test_dashboard_app_marks_broker_disconnected_when_heartbeat_is_stale(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("0.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="controlled",
        primary_responsive=True,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(111, 222, 333, 444),
    )

    scene = build_dashboard_scene(
        preview_layout,
        snapshot,
        broker_heartbeat_file=heartbeat_file,
    )

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[preview_layout.broker_panel] == COLOR_RED


def test_minimize_routes_omniminimize_command(cfg_path: Path):
    """Minimizing the dashboard writes the omniminimize command for the dispatch loop."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config)
    app_config = load_dashboard_app_config(manifest_path)
    launch_geo = DashboardLaunchGeometry(x=100, y=200, width=300, height=400)

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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
    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))), \
         patch("fun_time.dashboard_app.loading_screen_active", return_value=True), \
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))), \
         patch("fun_time.dashboard_app.loading_screen_active", return_value=True):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
        window = build_dashboard_window(app_config, launch_geometry=launch_geo)

    try:
        with patch.object(window, "_sync_own_topmost") as mock_sync:
            window._do_render(None, frozenset())
        mock_sync.assert_called_once_with(False)
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
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


def test_dashboard_app_hydrates_live_player_state():
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="controlled",
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", True),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(1, 2, 3, 4),
    )
    players = PlayerHydration(
        primary_path="primary.mp4",
        portrait_path="portrait.mp4",
        landscape_path="landscape.mp4",
        primary_responsive=True,
    )

    hydrated = hydrate_dashboard_snapshot(snapshot, players)

    assert hydrated.primary.path == "primary.mp4"
    assert hydrated.portrait.path == "portrait.mp4"
    assert hydrated.landscape.path == "landscape.mp4"
    assert hydrated.primary_responsive is True


def test_poll_players_reads_primary_from_nau_and_satellites_from_status_files(cfg_path: Path, tmp_path: Path):
    """The primary panel's video comes from Nau (which owns the primary display),
    while the two satellites come from their native status files."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, destination=tmp_path / "windows_bridge_launch.ini")
    app_config = load_dashboard_app_config(manifest_path)

    app_config.portrait_status_file.write_text(
        "video=po.mp4\nposition_ms=100\nduration_ms=1000\npaused=0\nlocked=0\n", encoding="utf-8")
    app_config.landscape_status_file.write_text(
        "video=l.mp4\nposition_ms=100\nduration_ms=1000\npaused=0\nlocked=0\n", encoding="utf-8")

    with patch("fun_time.dashboard_app.read_nau_status", return_value=NauStatus(video="p.mp4")):
        result = poll_players(app_config)

    assert isinstance(result, PlayerHydration)
    assert result.primary_path == "p.mp4"
    assert result.portrait_path == "po.mp4"
    assert result.landscape_path == "l.mp4"
    assert result.primary_responsive is True


def test_poll_players_marks_primary_unresponsive_when_nau_reports_no_video(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, destination=tmp_path / "windows_bridge_launch.ini")
    app_config = load_dashboard_app_config(manifest_path)

    with patch("fun_time.dashboard_app.read_nau_status", return_value=NauStatus()):
        result = poll_players(app_config)

    assert result.primary_responsive is False
    assert result.primary_path == ""


def test_dashboard_scene_has_help_action_label_and_hover(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    action_rects = {a: r for a, r in scene.actions}
    assert "help_reference" in action_rects
    assert action_rects["help_reference"] == layout.help_button

    text_at = {item.rect: item.text for item in scene.texts}
    assert text_at[layout.help_button] == "?"

    hover = {rect: text for rect, text in scene.hover_texts}
    assert layout.help_button in hover
    assert "hotkey" in hover[layout.help_button].lower()


def test_dashboard_scene_help_action_present_in_genau_mode(cfg_path: Path):
    """Help is a global control — present regardless of primary mode."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    assert "help_reference" in action_ids


def test_dashboard_scene_has_quit_and_omnipause_actions(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    action_ids = [action for action, _rect in scene.actions]
    assert "quit" in action_ids
    assert "omnipause_toggle" in action_ids
    assert "fmode_panel" in action_ids
    assert "voice_toggle" in action_ids


def test_dashboard_scene_draws_the_log_box_beside_the_dash_box(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    drawn = {item.rect for item in scene.rects}
    assert layout.dash_panel in drawn
    assert layout.log_panel in drawn
    # The log box is inert: a "Logs" title over ruled lines, and it triggers nothing.
    assert not any(rect == layout.log_panel for _action, rect in scene.actions)
    log_titles = [item for item in scene.texts if item.rect == layout.log_panel]
    assert [item.text for item in log_titles] == ["Logs"]
    assert log_titles[0].anchor == "n"
    log_rules = [
        line for line in scene.lines
        if line.points[0][0] == layout.log_panel.x + 4
    ]
    assert log_rules, "log box should be drawn with ruled stand-in lines"
    # The title took the top line's place, so the ruled lines start below it.
    assert min(line.points[0][1] for line in log_rules) >= layout.log_panel.y + 16


def test_dashboard_scene_titles_the_favs_browser_box(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    rfb_titles = [item for item in scene.texts if item.rect == layout.rfb_panel]
    assert [item.text for item in rfb_titles] == ["Favs Browser"]
    assert rfb_titles[0].anchor == "n"


def test_dashboard_scene_shows_the_app_name_lockup_top_left(cfg_path: Path):
    """The icon followed by "Fun Time", styled like the loading screen (pink,
    bold italic), a step larger than the box titles, in the top-left band."""
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    title = next(item for item in scene.texts if item.text == "Fun Time")
    assert title.rect == layout.app_title
    assert title.anchor == "w"
    # The wordmark is the loading screen's redder pink, not the logo's COLOR_PINK.
    assert title.color == COLOR_APP_TITLE
    assert title.color != COLOR_PINK
    assert title.font is not None and title.font.bold() and title.font.italic()
    # Larger than a box title (SIZE_SMALL, 9pt).
    assert title.font.pointSize() > 9
    # The app icon is drawn to its left.
    icon_rects = {item.rect for item in scene.images}
    assert layout.app_icon in icon_rects
    assert layout.app_icon.x + layout.app_icon.width <= layout.app_title.x


def test_dashboard_scene_omnipause_button_shows_pause_icon_when_not_paused(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="controlled",
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(preview_layout, snapshot)

    omnipause_texts = [item for item in scene.texts if item.rect == preview_layout.omnipause_button]
    assert len(omnipause_texts) == 1
    assert omnipause_texts[0].text == "\u23F8"  # pause icon
    omnipause_rects = [item for item in scene.rects if item.rect == preview_layout.omnipause_button]
    assert omnipause_rects[0].fill == COLOR_PANEL


def test_dashboard_scene_panel_labels_are_top_justified(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    panel_rects = {preview_layout.landscape_panel, preview_layout.portrait_panel, preview_layout.primary_panel}
    panel_labels = [item for item in scene.texts if item.rect in panel_rects]
    assert len(panel_labels) == 3
    for label in panel_labels:
        assert label.anchor == "n", f"Expected anchor='n' for '{label.text}', got '{label.anchor}'"


def test_dashboard_scene_omnipause_button_shows_play_icon_when_paused(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode="nau",
        osr2_mode="controlled",
        primary_responsive=False,
        omni_paused=True,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(preview_layout, snapshot)

    omnipause_texts = [item for item in scene.texts if item.rect == preview_layout.omnipause_button]
    assert len(omnipause_texts) == 1
    assert omnipause_texts[0].text == "\u25B6"  # play icon
    omnipause_rects = [item for item in scene.rects if item.rect == preview_layout.omnipause_button]
    assert omnipause_rects[0].fill == COLOR_PANEL


def test_lighten_color_adds_to_each_channel():
    result = lighten_color(QColor(0x2A, 0x30, 0x38), 50)
    assert (result.red(), result.green(), result.blue()) == (0x5C, 0x62, 0x6A)
    result2 = lighten_color(QColor(0, 0, 0), 30)
    assert (result2.red(), result2.green(), result2.blue()) == (30, 30, 30)


def test_lighten_color_caps_at_255():
    result = lighten_color(QColor(240, 240, 240), 50)
    assert (result.red(), result.green(), result.blue()) == (255, 255, 255)


def test_dashboard_scene_lock_buttons_use_icon(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    lock_texts = [
        item for item in scene.texts
        if item.rect in (preview_layout.portrait_lock, preview_layout.landscape_lock)
    ]
    assert len(lock_texts) == 2
    for item in lock_texts:
        assert item.text == ICON_LOCK, f"Expected lock icon, got '{item.text}'"


def test_dashboard_scene_trash_buttons_use_icon(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    trash_texts = [
        item for item in scene.texts
        if item.rect in (preview_layout.portrait_trash, preview_layout.landscape_trash)
    ]
    assert len(trash_texts) == 2
    for item in trash_texts:
        assert item.text == ICON_TRASH, f"Expected trash icon, got '{item.text}'"


def test_dashboard_scene_trash_buttons_are_not_yellow(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[preview_layout.portrait_trash] != COLOR_YELLOW
    assert fills[preview_layout.landscape_trash] != COLOR_YELLOW
    assert fills[preview_layout.portrait_trash] == COLOR_PANEL
    assert fills[preview_layout.landscape_trash] == COLOR_PANEL


def test_dashboard_scene_pressed_button_has_lighter_fill(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    scene_normal = build_dashboard_scene(preview_layout)
    scene_pressed = build_dashboard_scene(preview_layout, pressed_actions=frozenset({"portrait_prev"}))

    normal_fills = {item.rect: item.fill for item in scene_normal.rects}
    pressed_fills = {item.rect: item.fill for item in scene_pressed.rects}
    assert pressed_fills[preview_layout.portrait_prev] != normal_fills[preview_layout.portrait_prev]
    assert pressed_fills[preview_layout.portrait_prev] == lighten_color(COLOR_PANEL)
    # Non-pressed buttons should keep their normal fill
    assert pressed_fills[preview_layout.portrait_next] == normal_fills[preview_layout.portrait_next]


def _make_snapshot(*, primary_mode: str = "nau") -> DashboardSnapshot:
    return DashboardSnapshot(
        f_mode_enabled=False,
        primary_mode=primary_mode,
        osr2_mode="auto",
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )


def _make_layout(cfg_path: Path) -> DashboardPreviewLayout:
    config = load_config(cfg_path)
    return compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )


def _cable_lines(scene) -> list:
    return [line for line in scene.lines if line.color == COLOR_CABLE]


def test_dashboard_scene_cable_is_simple_straight_line(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot()

    scene = build_dashboard_scene(layout, snapshot)

    # 1 straight line, 2 endpoint ovals (sockets), 0 arcs
    cables = _cable_lines(scene)
    assert len(cables) == 1, "Cable: one straight line"
    assert len(cables[0].points) == 2
    assert len(scene.ovals) == 2, "2 endpoint sockets"
    assert len(scene.arcs) == 0


def test_dashboard_scene_cable_spans_osr2_to_primary(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot()

    scene = build_dashboard_scene(layout, snapshot)

    osr2_right = layout.osr2_panel.x + layout.osr2_panel.width
    primary_left = layout.primary_panel.x
    # Line starts at OSR2 right edge, ends at Primary left edge
    cable = _cable_lines(scene)[0]
    assert cable.points[0][0] == osr2_right
    assert cable.points[-1][0] == primary_left


def test_dashboard_scene_genau_activate_has_rect(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot()

    scene = build_dashboard_scene(layout, snapshot)

    assert any(item.rect == layout.genau_mode_toggle for item in scene.rects)


def test_dashboard_scene_genau_activate_press_lightens_color(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot()

    scene_normal = build_dashboard_scene(layout, snapshot)
    scene_pressed = build_dashboard_scene(
        layout, snapshot, pressed_actions=frozenset({"genau_activate"}),
    )

    normal_fills = {item.rect: item.fill for item in scene_normal.rects}
    pressed_fills = {item.rect: item.fill for item in scene_pressed.rects}
    assert pressed_fills[layout.genau_mode_toggle] == lighten_color(COLOR_PANEL)
    assert pressed_fills[layout.genau_mode_toggle] != normal_fills[layout.genau_mode_toggle]


def test_dashboard_scene_chips_have_hover_texts(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    hover = {rect: text for rect, text in scene.hover_texts}
    assert layout.broker_panel in hover
    assert layout.fmode_panel in hover
    assert "broker" in hover[layout.broker_panel].lower()


def test_dashboard_scene_every_action_has_a_tooltip(cfg_path: Path):
    layout = _make_layout(cfg_path)

    for primary_mode in ("nau", "genau", "hybrid"):
        scene = build_dashboard_scene(layout, _make_snapshot(primary_mode=primary_mode))
        hover = {rect: text for rect, text in scene.hover_texts}
        for action_id, rect in scene.actions:
            assert hover.get(rect), f"action {action_id!r} ({primary_mode}) has no tooltip"


def test_dashboard_scene_default_cable_connected_without_snapshot(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    assert len(_cable_lines(scene)) == 1, "Default (no snapshot) should show cable"


def test_osr2_highlights_green_when_funscript_playing(cfg_path: Path):
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    primary_root = config.paths.nau_library_dirs[0]
    primary_root.mkdir(parents=True, exist_ok=True)
    primary_path = primary_root / "vid.mp4"
    primary_path.write_text("v", encoding="utf-8")
    script_path = Path(
        str(primary_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")
    ) / "vid.funscript"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("s", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot(str(primary_path), False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.primary_panel] == COLOR_GREEN
    assert fills[layout.osr2_panel] == COLOR_GREEN


def test_osr2_auto_mode_stays_pink_even_with_funscript(cfg_path: Path):
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    primary_root = config.paths.nau_library_dirs[0]
    primary_root.mkdir(parents=True, exist_ok=True)
    primary_path = primary_root / "vid.mp4"
    primary_path.write_text("v", encoding="utf-8")
    script_path = Path(
        str(primary_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")
    ) / "vid.funscript"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("s", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="genau",
        osr2_mode="auto", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot(str(primary_path), False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.osr2_panel] == COLOR_PINK, "Auto mode must stay pink even with funscript"


def test_genau_label_says_genau():
    from fun_time.dashboard_state import LABEL_PRIMARY_GENAU
    assert LABEL_PRIMARY_GENAU == "Genau"


def test_quit_button_uses_neutral_grey(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.quit_button] == COLOR_PANEL


def test_active_chips_and_locks_use_correct_colors(cfg_path: Path):
    import time as _time
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text(str(_time.time()), encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=True, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", True), landscape=DashboardPanelSnapshot("", True),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot, broker_heartbeat_file=heartbeat_file)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.broker_panel] == COLOR_BLUE
    assert fills[layout.portrait_lock] == COLOR_GREEN
    assert fills[layout.landscape_lock] == COLOR_GREEN


def test_osr2_label_is_top_justified(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    osr2_texts = [item for item in scene.texts if item.rect == layout.osr2_panel]
    assert osr2_texts[0].anchor == "n"


def test_osr2_controlled_with_funscript_shows_funscript_control(cfg_path: Path):
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    primary_root = config.paths.nau_library_dirs[0]
    primary_root.mkdir(parents=True, exist_ok=True)
    primary_path = primary_root / "vid.mp4"
    primary_path.write_text("v", encoding="utf-8")
    script_path = Path(
        str(primary_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")
    ) / "vid.funscript"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("s", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot(str(primary_path), False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    texts = {item.text for item in scene.texts}
    assert "OSR2\n(funscript\ncontrol)" in texts


def test_osr2_controlled_without_funscript_shows_idle(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    texts = {item.text for item in scene.texts}
    assert "OSR2\n(idle; no\nfunscript)" in texts


def test_osr2_auto_mode_shows_parenthesized_auto(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot()

    scene = build_dashboard_scene(layout, snapshot)

    texts = {item.text for item in scene.texts}
    assert "OSR2\n(auto)" in texts


def test_osr2_off_mode_shows_off_label_and_dim_color(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="off", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    texts = {item.text for item in scene.texts}
    assert "OSR2\n(off)" in texts
    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.osr2_panel] == COLOR_PANEL


def test_the_panels_name_the_players_their_windows_do(cfg_path: Path):
    """The Dash calls each side what its window calls itself, so the panel and
    the Alt-Tab entry are recognisably the same thing.  Portrait's panel is a
    narrow column, so its name wraps rather than being shortened."""
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    texts = {item.text for item in scene.texts}
    assert "Portrait\nAI Player" in texts
    assert "Landscape AI Player" in texts


def test_omnipause_resume_button_is_not_green_when_paused(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=False, omni_paused=True,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.omnipause_button] == COLOR_PANEL


def test_nau_mode_shows_nau_buttons_not_quarter(cfg_path: Path):
    """Nau box should show file dialog, clipper, and nudge buttons — not 1/4."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="nau")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    assert "open_file_dialog" in action_ids
    assert "clipper_save" in action_ids
    assert "primary_nudge_prev" in action_ids
    assert "primary_nudge_next" in action_ids
    assert "quarter_button" not in action_ids


def test_genau_mode_shows_quarter_not_nau_buttons(cfg_path: Path):
    """Genau box should show 1/4 button only."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    assert "quarter_button" in action_ids
    assert "open_file_dialog" not in action_ids
    assert "clipper_save" not in action_ids
    assert "primary_nudge_prev" not in action_ids
    assert "primary_nudge_next" not in action_ids


def test_default_scene_shows_nau_buttons(cfg_path: Path):
    """Default (no snapshot) is Nau, so should show Nau buttons."""
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    action_ids = [a for a, _r in scene.actions]
    assert "open_file_dialog" in action_ids
    assert "quarter_button" not in action_ids


def test_nau_buttons_text_labels(cfg_path: Path):
    """File dialog button shows folder icon, nudge shows - and +, clipper is an image."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="nau")

    scene = build_dashboard_scene(layout, snapshot)

    text_at = {item.rect: item.text for item in scene.texts}
    assert text_at[layout.open_file_dialog] == "\U0001F4C2"
    assert text_at[layout.primary_nudge_prev] == "\u2212"  # minus sign
    assert text_at[layout.primary_nudge_next] == "+"
    # Clipper save is now an image item, not a text item
    image_rects = {item.rect for item in scene.images}
    assert layout.clipper_save in image_rects


def test_primary_nudge_buttons_are_adjacent_not_edge_justified(cfg_path: Path):
    """Nudge buttons should be next to each other, centered — not at panel edges."""
    layout = _make_layout(cfg_path)

    gap = layout.primary_nudge_next.x - (layout.primary_nudge_prev.x + layout.primary_nudge_prev.width)
    assert gap <= 8, f"Nudge buttons should be adjacent (gap={gap})"
    # Both should be roughly centered in the primary panel
    panel_cx = layout.primary_panel.x + layout.primary_panel.width // 2
    nudge_cx = (layout.primary_nudge_prev.x + layout.primary_nudge_next.x + layout.primary_nudge_next.width) // 2
    assert abs(panel_cx - nudge_cx) <= 2, "Nudge pair should be centered in panel"


def test_nau_buttons_light_up_when_pressed(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="nau")

    scene_normal = build_dashboard_scene(layout, snapshot)
    scene_pressed = build_dashboard_scene(
        layout, snapshot, pressed_actions=frozenset({"open_file_dialog", "clipper_save", "primary_nudge_prev", "primary_nudge_next"}),
    )

    normal_fills = {item.rect: item.fill for item in scene_normal.rects}
    pressed_fills = {item.rect: item.fill for item in scene_pressed.rects}
    for rect_name in ("open_file_dialog", "clipper_save", "primary_nudge_prev", "primary_nudge_next"):
        rect = getattr(layout, rect_name)
        assert pressed_fills[rect] == lighten_color(COLOR_PANEL), f"{rect_name} should light up"
        assert pressed_fills[rect] != normal_fills[rect], f"{rect_name} pressed should differ from normal"


def test_nudge_buttons_above_file_dialog_clipper_below(cfg_path: Path):
    """Nudge buttons should be above the file dialog; clipper should be below."""
    layout = _make_layout(cfg_path)

    assert layout.primary_nudge_prev.y + layout.primary_nudge_prev.height <= layout.open_file_dialog.y
    assert layout.primary_nudge_next.y + layout.primary_nudge_next.height <= layout.open_file_dialog.y
    assert layout.open_file_dialog.y + layout.open_file_dialog.height <= layout.clipper_save.y


def test_nudge_buttons_clear_of_title_area(cfg_path: Path):
    """Nudge buttons (topmost Nau buttons) must not overlap the panel label."""
    layout = _make_layout(cfg_path)

    # Title is anchored at "n" — roughly the top 14px of the panel
    title_bottom = layout.primary_panel.y + 14
    assert layout.primary_nudge_prev.y >= title_bottom, (
        f"nudge_prev.y={layout.primary_nudge_prev.y} overlaps title_bottom={title_bottom}"
    )


def test_quarter_button_matches_file_dialog_y(cfg_path: Path):
    """quarter_button and open_file_dialog should share the same y so mode switch is smooth."""
    layout = _make_layout(cfg_path)

    assert layout.quarter_button.y == layout.open_file_dialog.y


def test_primary_nav_buttons_centered_on_file_dialog(cfg_path: Path):
    """< and > buttons should be vertically centered on the file dialog / quarter button row."""
    layout = _make_layout(cfg_path)

    mid_row_center = layout.open_file_dialog.y + layout.open_file_dialog.height // 2
    prev_center = layout.primary_prev.y + layout.primary_prev.height // 2
    next_center = layout.primary_next.y + layout.primary_next.height // 2
    assert prev_center == mid_row_center
    assert next_center == mid_row_center


def test_scene_contains_rfb_box(cfg_path: Path):
    layout = _make_layout(cfg_path)
    scene = build_dashboard_scene(layout)

    fills = {item.rect: item.fill for item in scene.rects}
    assert layout.rfb_panel in fills


def test_scene_contains_primary_shadow_behind_primary(cfg_path: Path):
    layout = _make_layout(cfg_path)
    scene = build_dashboard_scene(layout)

    rects_in_order = [item.rect for item in scene.rects]
    shadow_idx = rects_in_order.index(layout.primary_shadow)
    primary_idx = rects_in_order.index(layout.primary_panel)
    # Shadow must be drawn before primary (underneath)
    assert shadow_idx < primary_idx

    outlines = {item.rect: item.outline for item in scene.rects}
    # Shadow uses a dimmer outline than default
    assert outlines[layout.primary_shadow] == COLOR_CABLE_DIM


def test_genau_mode_has_param_and_cruise_and_shape_actions(cfg_path: Path):
    """Genau box should include AMP/CTR/SPD up/down, cruise, and shape actions."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    for expected in (
        "genau_amplitude_up", "genau_amplitude_down",
        "genau_center_up", "genau_center_down",
        "genau_speed_up", "genau_speed_down",
        "genau_toggle_cruise", "genau_cycle_shape",
    ):
        assert expected in action_ids, f"missing action {expected}"


def test_genau_param_labels_are_rotated(cfg_path: Path):
    """AMP/CTR/SPD labels should be rotated 90° (not horizontal)."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    label_items = [item for item in scene.texts if item.text in ("AMP", "CTR", "SPD")]
    assert len(label_items) == 3, f"Expected 3 param labels, found {[i.text for i in label_items]}"
    for item in label_items:
        assert item.rotation == 90, f"{item.text} should be rotated 90°"


def test_genau_cruise_button_blue_when_active(cfg_path: Path):
    from fun_time.dashboard_runtime import GenauStatus
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(
        layout, snapshot,
        genau_status=GenauStatus(cruise_active=True, shape="sine"),
    )

    fills = {item.rect: item.fill for item in scene.rects}
    from shared_ui.colors import BLUE
    assert fills[layout.genau_cruise] == BLUE


def test_genau_cruise_button_neutral_when_inactive(cfg_path: Path):
    from fun_time.dashboard_runtime import GenauStatus
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(
        layout, snapshot,
        genau_status=GenauStatus(cruise_active=False, shape="sine"),
    )

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.genau_cruise] == COLOR_PANEL


def test_genau_auto_advance_button_sits_beside_cruise_in_both_modes(cfg_path: Path):
    """Cruise varies the stroke, auto advance moves on — one button each."""
    layout = _make_layout(cfg_path)

    genau = build_dashboard_scene(layout, _make_snapshot(primary_mode="genau"))
    g_actions = {a: r for a, r in genau.actions}
    assert g_actions.get("genau_toggle_auto_advance") == layout.genau_advance
    assert {i.rect: i.text for i in genau.texts}[layout.genau_advance] == "aa"

    hybrid = build_dashboard_scene(layout, _make_snapshot(primary_mode="hybrid"))
    h_actions = {a: r for a, r in hybrid.actions}
    assert h_actions.get("genau_toggle_auto_advance") == layout.hybrid_advance


def test_genau_auto_advance_button_lights_when_armed_and_when_held(cfg_path: Path):
    """A held clip is still armed, so the button stays lit — in another colour,
    the way the Genau HUD's own AA badge does."""
    from fun_time.dashboard_runtime import GenauStatus
    from shared_ui.colors import BLUE
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    def _fill(status):
        scene = build_dashboard_scene(layout, snapshot, genau_status=status)
        return {item.rect: item.fill for item in scene.rects}[layout.genau_advance]

    assert _fill(GenauStatus()) == COLOR_PANEL
    assert _fill(GenauStatus(auto_advance_active=True)) == BLUE
    held = _fill(GenauStatus(auto_advance_active=True, clip_locked=True))
    assert held not in (COLOR_PANEL, BLUE)


def test_takeover_toggle_in_nau_and_hybrid_not_genau(cfg_path: Path):
    """The takeover toggle belongs where Genau isn't the primary: Nau and Hybrid."""
    layout = _make_layout(cfg_path)

    # Genau mode: cc owns the bottom-left; no takeover toggle (Genau already active).
    genau = build_dashboard_scene(layout, _make_snapshot(primary_mode="genau"))
    g_actions = {a: r for a, r in genau.actions}
    g_text = {item.rect: item.text for item in genau.texts}
    assert "genau_toggle_auto" not in g_actions
    assert g_actions.get("genau_toggle_cruise") == layout.genau_cruise
    assert g_text[layout.genau_cruise] == "cc"

    # Nau mode: takeover toggle at the bottom-left, green when allowed / red when not.
    nau = build_dashboard_scene(layout, _make_snapshot(primary_mode="nau"), genau_takeover_allowed=True)
    v_actions = {a: r for a, r in nau.actions}
    v_text = {item.rect: item.text for item in nau.texts}
    assert v_actions.get("genau_toggle_auto") == layout.genau_takeover
    assert "genau_toggle_cruise" not in v_actions
    assert v_text[layout.genau_takeover] == "GA"
    assert {i.rect: i.fill for i in nau.rects}[layout.genau_takeover] == COLOR_GREEN
    nau_off = build_dashboard_scene(layout, _make_snapshot(primary_mode="nau"), genau_takeover_allowed=False)
    assert {i.rect: i.fill for i in nau_off.rects}[layout.genau_takeover] == COLOR_RED

    # Hybrid mode: takeover bottom-left, cc shifted to hybrid_cruise beside it.
    hybrid = build_dashboard_scene(layout, _make_snapshot(primary_mode="hybrid"))
    h_actions = {a: r for a, r in hybrid.actions}
    assert h_actions.get("genau_toggle_auto") == layout.genau_takeover
    assert h_actions.get("genau_toggle_cruise") == layout.hybrid_cruise


def test_genau_shape_button_has_neutral_fill(cfg_path: Path):
    """Shape button must use neutral fill so the waveform icon is visible."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.genau_shape] == COLOR_PANEL


def test_genau_buttons_greyed_at_limits(cfg_path: Path):
    """When a param is at max, its up button text should be muted; same for min/down."""
    from fun_time.dashboard_runtime import GenauStatus
    from shared_ui.colors import TEXT_MUTED
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(
        layout, snapshot,
        genau_status=GenauStatus(amp_at_max=True, ctr_at_min=True),
    )

    text_at = {item.rect: item for item in scene.texts}
    # AMP up should be muted (at max), AMP down should be normal
    assert text_at[layout.genau_amp_up].color == TEXT_MUTED
    assert text_at[layout.genau_amp_down].color != TEXT_MUTED
    # CTR down should be muted (at min), CTR up should be normal
    assert text_at[layout.genau_ctr_down].color == TEXT_MUTED
    assert text_at[layout.genau_ctr_up].color != TEXT_MUTED
    # SPD neither — both normal
    assert text_at[layout.genau_spd_up].color != TEXT_MUTED
    assert text_at[layout.genau_spd_down].color != TEXT_MUTED

    # Disabled buttons must not be clickable (no action = no highlight)
    action_ids = [a for a, _r in scene.actions]
    assert "genau_amplitude_up" not in action_ids, "at-max button should not be clickable"
    assert "genau_amplitude_down" in action_ids, "not-at-min button should be clickable"
    assert "genau_center_down" not in action_ids, "at-min button should not be clickable"
    assert "genau_center_up" in action_ids, "not-at-max button should be clickable"


def test_hybrid_spd_buttons_stay_live_at_genau_limits(cfg_path: Path):
    """In hybrid the SPD keys route per-stretch (Nau's video or Genau), so they
    stay clickable and un-greyed even when Genau's own speed is maxed/floored —
    unlike amp/center, which only ever drive Genau."""
    from fun_time.dashboard_runtime import GenauStatus
    from shared_ui.colors import TEXT_MUTED
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="hybrid")

    scene = build_dashboard_scene(
        layout, snapshot,
        genau_status=GenauStatus(spd_at_max=True, spd_at_min=True, amp_at_max=True),
    )

    action_ids = [a for a, _r in scene.actions]
    assert "genau_speed_up" in action_ids and "genau_speed_down" in action_ids
    text_at = {item.rect: item for item in scene.texts}
    assert text_at[layout.hybrid_genau_spd_up].color != TEXT_MUTED
    assert text_at[layout.hybrid_genau_spd_down].color != TEXT_MUTED
    # amp still gates on Genau's own limit in hybrid.
    assert "genau_amplitude_up" not in action_ids


def test_nau_mode_does_not_show_genau_param_actions(cfg_path: Path):
    """Nau mode should NOT have AMP/CTR/SPD or cruise/shape actions."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="nau")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    for unexpected in (
        "genau_amplitude_up", "genau_amplitude_down",
        "genau_toggle_cruise", "genau_cycle_shape",
    ):
        assert unexpected not in action_ids, f"unexpected action {unexpected} in Nau mode"


# ---------------------------------------------------------------------------
# DashboardWidget tests
# ---------------------------------------------------------------------------

def test_dashboard_widget_emits_action_on_click(cfg_path: Path):
    """Clicking inside an action rect should emit action_triggered with the action ID."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QApplication
    from fun_time.dashboard_app import DashboardWidget

    layout = _make_layout(cfg_path)
    scene = build_dashboard_scene(layout)

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


def test_voice_panel_blue_when_active(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
        voice_active=True,
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.voice_panel] == COLOR_BLUE


def test_voice_panel_neutral_when_inactive(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
        voice_active=False,
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.voice_panel] == COLOR_PANEL


def test_broker_panel_blue_when_running(cfg_path: Path):
    import time as _time
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text(str(_time.time()), encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, primary_mode="nau",
        osr2_mode="controlled", primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot, broker_heartbeat_file=heartbeat_file)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.broker_panel] == COLOR_BLUE


def test_voice_panel_shows_mic_icon(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    # The mic is a drawn glyph (a QPixmap image), not a letter or emoji text.
    voice_images = [item for item in scene.images if item.rect == layout.voice_panel]
    assert len(voice_images) == 1
    assert not voice_images[0].pixmap.isNull()
    assert not [item for item in scene.texts if item.rect == layout.voice_panel]


def test_voice_panel_has_hover_text(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    hover = {rect: text for rect, text in scene.hover_texts}
    assert layout.voice_panel in hover
    assert "voice" in hover[layout.voice_panel].lower()


def test_dashboard_widget_ignores_click_outside_actions(cfg_path: Path):
    """Clicking outside any action rect should not emit."""
    from PyQt6.QtCore import QPoint
    from fun_time.dashboard_app import DashboardWidget

    layout = _make_layout(cfg_path)
    scene = build_dashboard_scene(layout)

    widget = DashboardWidget()
    widget.set_scene(scene)
    received: list[str] = []
    widget.action_triggered.connect(received.append)

    from unittest.mock import MagicMock
    event = MagicMock()
    event.position.return_value = QPoint(0, 0).toPointF()
    widget.mousePressEvent(event)

    assert received == []


def test_nau_mode_has_hybrid_activate_action(cfg_path: Path):
    """Nau mode should have a hybrid_activate action at the hybrid_mode_button."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="nau")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    assert "hybrid_activate" in action_ids
    action_rects = {a: r for a, r in scene.actions}
    assert action_rects["hybrid_activate"] == layout.hybrid_mode_button


def test_nau_mode_has_h_text_on_hybrid_button(cfg_path: Path):
    """Nau mode should show 'h' text at the hybrid_mode_button position."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="nau")

    scene = build_dashboard_scene(layout, snapshot)

    text_at = {item.rect: item.text for item in scene.texts}
    assert text_at[layout.hybrid_mode_button] == "h"


def test_genau_mode_has_hybrid_activate_action(cfg_path: Path):
    """Genau mode should have a hybrid_activate action at the hybrid_mode_button."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    assert "hybrid_activate" in action_ids
    action_rects = {a: r for a, r in scene.actions}
    assert action_rects["hybrid_activate"] == layout.hybrid_mode_button


def test_genau_mode_has_nau_activate_at_toggle(cfg_path: Path):
    """Genau mode: genau_mode_toggle shows 'Nau' text and maps to nau_activate."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="genau")

    scene = build_dashboard_scene(layout, snapshot)

    action_rects = {a: r for a, r in scene.actions}
    assert "nau_activate" in action_rects
    assert action_rects["nau_activate"] == layout.genau_mode_toggle
    text_at = {item.rect: item.text for item in scene.texts}
    assert text_at[layout.genau_mode_toggle] == "Nau"


def test_genau_mode_toggle_action_switches_by_mode(cfg_path: Path):
    """The genau_mode_toggle switches to Genau from Nau mode, and back to Nau from Genau mode."""
    from fun_time.dashboard_actions import GENAU_ACTIVATE, NAU_ACTIVATE
    layout = _make_layout(cfg_path)

    nau_actions = {a: r for a, r in build_dashboard_scene(layout, _make_snapshot(primary_mode="nau")).actions}
    assert nau_actions.get(GENAU_ACTIVATE) == layout.genau_mode_toggle
    assert NAU_ACTIVATE not in nau_actions or nau_actions[NAU_ACTIVATE] != layout.genau_mode_toggle

    genau_actions = {a: r for a, r in build_dashboard_scene(layout, _make_snapshot(primary_mode="genau")).actions}
    assert genau_actions.get(NAU_ACTIVATE) == layout.genau_mode_toggle
    assert GENAU_ACTIVATE not in genau_actions or genau_actions[GENAU_ACTIVATE] != layout.genau_mode_toggle


def test_nau_mode_exposes_record_button_absent_in_genau_and_hybrid(cfg_path: Path):
    """The nau_record button appears only in Nau mode's primary panel."""
    from fun_time.dashboard_actions import NAU_RECORD
    layout = _make_layout(cfg_path)

    nau_actions = {a: r for a, r in build_dashboard_scene(layout, _make_snapshot(primary_mode="nau")).actions}
    assert nau_actions.get(NAU_RECORD) == layout.nau_record

    genau_action_ids = [a for a, _r in build_dashboard_scene(layout, _make_snapshot(primary_mode="genau")).actions]
    assert NAU_RECORD not in genau_action_ids

    hybrid_action_ids = [a for a, _r in build_dashboard_scene(layout, _make_snapshot(primary_mode="hybrid")).actions]
    assert NAU_RECORD not in hybrid_action_ids


def test_hybrid_mode_shows_all_nau_and_genau_actions(cfg_path: Path):
    """Hybrid mode should include both Nau and genau actions."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="hybrid")

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    # Nau actions
    assert "primary_nudge_prev" in action_ids
    assert "primary_nudge_next" in action_ids
    assert "clipper_save" in action_ids
    # Genau actions
    assert "quarter_button" in action_ids
    assert "genau_toggle_cruise" in action_ids
    assert "genau_cycle_shape" in action_ids


def test_hybrid_mode_uses_hybrid_positioned_rects(cfg_path: Path):
    """Hybrid mode should use hybrid_quarter_button and hybrid_open_file_dialog rects."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="hybrid")

    scene = build_dashboard_scene(layout, snapshot)

    action_rects = {a: r for a, r in scene.actions}
    assert action_rects["quarter_button"] == layout.hybrid_quarter_button
    assert action_rects["open_file_dialog"] == layout.hybrid_open_file_dialog


def test_hybrid_mode_toggle_buttons(cfg_path: Path):
    """Hybrid mode: hybrid_mode_button maps to nau_activate, genau_mode_toggle maps to genau_activate."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="hybrid")

    scene = build_dashboard_scene(layout, snapshot)

    action_rects = {a: r for a, r in scene.actions}
    assert "nau_activate" in action_rects
    assert action_rects["nau_activate"] == layout.hybrid_mode_button
    assert "genau_activate" in action_rects
    assert action_rects["genau_activate"] == layout.genau_mode_toggle

    text_at = {item.rect: item.text for item in scene.texts}
    assert text_at[layout.hybrid_mode_button] == "Nau"


def test_hybrid_mode_has_genau_param_labels(cfg_path: Path):
    """Hybrid mode should show AMP/CTR/SPD labels at hybrid positions."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_mode="hybrid")

    scene = build_dashboard_scene(layout, snapshot)

    text_at = {item.rect: item.text for item in scene.texts}
    assert text_at[layout.hybrid_genau_amp_label] == "AMP"
    assert text_at[layout.hybrid_genau_ctr_label] == "CTR"
    assert text_at[layout.hybrid_genau_spd_label] == "SPD"


class TestLoadingScreenActive:
    def test_true_while_progress_file_present(self, tmp_path):
        from fun_time.dashboard_app import loading_screen_active
        (tmp_path / "startup_progress.txt").write_text("1/9", encoding="utf-8")
        assert loading_screen_active(tmp_path) is True

    def test_false_once_progress_file_gone(self, tmp_path):
        from fun_time.dashboard_app import loading_screen_active
        assert loading_screen_active(tmp_path) is False
