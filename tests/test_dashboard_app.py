from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PyQt6.QtGui import QColor

from fun_time.manifest import write_windows_bridge_manifest
from fun_time.dashboard_app import (
    COLOR_GREEN,
    COLOR_CABLE,
    COLOR_CABLE_DIM,
    COLOR_RED,
    COLOR_PINK,
    COLOR_PANEL,
    COLOR_YELLOW,
    ICON_LOCK,
    ICON_TRASH,
    DashboardArcItem,
    DashboardLaunchGeometry,
    DashboardLineItem,
    DashboardOvalItem,
    VlcHydration,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
    build_dashboard_window,
    hydrate_dashboard_snapshot,
    lighten_color,
    load_dashboard_app_config,
    poll_vlc,
    resolve_logical_monitor_sizes,
    write_dashboard_command,
)
from fun_time.dashboard_runtime import DashboardPanelSnapshot, DashboardSnapshot, DashboardWindowSnapshot
from fun_time.dashboard_layout import DashboardPreviewLayout, Size, compute_dashboard_preview_layout
from fun_time import load_config


def test_dashboard_app_loads_layout_from_manifest(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass", destination=tmp_path / "windows_bridge_launch.ini")

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.layout.main_monitor == 1
    assert app_config.layout.secondary_monitor == 2
    assert app_config.layout.landscape_width_ratio == config.layout.landscape_width_ratio
    assert app_config.primary_sources == "|".join(str(path) for path in config.paths.primary_vlc_dirs)
    assert app_config.favs_file == config.paths.favs_file
    assert app_config.primary_vlc_port == config.vlc.primary_vlc_http_port
    assert app_config.portrait_vlc_port == config.vlc.vlc2_http_port
    assert app_config.landscape_vlc_port == config.vlc.vlc3_http_port
    assert app_config.vlc_password == "vlc-pass"
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
        robot_link_enabled=False,
        primary_uses_genau=False,
        osr2_mode="manual",
        mfp_alive=False,
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
    assert not any(item.text == "Fun Time" for item in scene.texts)
    assert len(scene.lines) == 2, "Default scene should show connected cable (two halves)"


def test_dashboard_app_scene_uses_runtime_snapshot_when_available(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    primary_root = config.paths.primary_vlc_dirs[0]
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
        robot_link_enabled=False,
        primary_uses_genau=False,
        osr2_mode="auto",
        mfp_alive=True,
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
    assert len(scene.lines) == 2, "Broken cable should have two segments"
    assert "Non-AI VLC" in texts
    assert "Portrait\nAI VLC" in texts
    assert not any(".mp4" in item.text for item in scene.texts)
    assert fills[preview_layout.primary_panel] == COLOR_GREEN
    assert fills[preview_layout.portrait_panel] == COLOR_GREEN
    assert any(action == "portrait_lock" for action, _rect in scene.actions)
    assert any(action == "link_toggle" for action, _rect in scene.actions)


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
        robot_link_enabled=False,
        primary_uses_genau=False,
        osr2_mode="auto",
        mfp_alive=True,
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
        robot_link_enabled=False,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=False,
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
    snapshot = _make_snapshot(primary_uses_genau=True)

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.quarter_button] == COLOR_PANEL


def test_genau_panel_is_pink_when_active(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_uses_genau=True)

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.primary_panel] == COLOR_PINK


def test_dashboard_app_writes_commands_for_click_actions(tmp_path: Path):
    command_file = tmp_path / "state" / "dashboard_cmd.txt"

    write_dashboard_command(command_file, "portrait_next")

    assert command_file.read_text(encoding="utf-8") == "portrait_next"


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
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=False,
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


def test_mfp_shows_green_when_alive_responsive_and_broker_fresh(cfg_path: Path):
    """MFP panel must be COLOR_GREEN when all three conditions are met:
    mfp_alive=True, primary_responsive=True, and broker heartbeat fresh."""
    import time

    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text(str(time.time()), encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=True,
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
    assert fills[preview_layout.mfp_panel] == COLOR_GREEN


def test_hydrate_sets_mfp_alive_true_for_current_process():
    """hydrate_dashboard_snapshot must set mfp_alive=True when given a valid PID."""
    import os
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=False,  # start as False
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(111, 222, 333, 444),
    )
    vlc = VlcHydration()
    hydrated = hydrate_dashboard_snapshot(snapshot, vlc, mfp_pid=os.getpid())
    assert hydrated.mfp_alive is True, f"is_process_alive({os.getpid()}) returned False"


def test_hydrate_sets_mfp_alive_false_for_zero_pid():
    """hydrate_dashboard_snapshot with mfp_pid=0 must set mfp_alive=False."""
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=True,  # start as True
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(111, 222, 333, 444),
    )
    vlc = VlcHydration()
    hydrated = hydrate_dashboard_snapshot(snapshot, vlc, mfp_pid=0)
    assert hydrated.mfp_alive is False


def test_dashboard_app_marks_broker_and_mfp_disconnected_when_heartbeat_is_stale(cfg_path: Path):
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
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=True,
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
    assert fills[preview_layout.mfp_panel] == COLOR_RED


def test_dashboard_window_decorations_and_close_handler(cfg_path: Path):
    """Window must show in taskbar (WS_EX_APPWINDOW) and close handler writes exit."""
    import ctypes
    from fun_time.dashboard_app import DashboardWindow

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
        from PyQt6.QtGui import QCloseEvent
        window.closeEvent(QCloseEvent())
        assert ahk_cmd_file.exists(), "Close handler should have written ahk_cmd.txt"
        assert ahk_cmd_file.read_text(encoding="utf-8") == "exit"
    finally:
        window.close()


def test_dashboard_app_hydrates_live_vlc_state():
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=True,
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", True),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(1, 2, 3, 4),
    )
    vlc = VlcHydration(
        primary_path="primary.mp4",
        portrait_path="portrait.mp4",
        landscape_path="landscape.mp4",
        primary_responsive=True,
    )

    with patch("fun_time.dashboard_app.is_process_alive", return_value=False):
        hydrated = hydrate_dashboard_snapshot(snapshot, vlc, mfp_pid=123)

    assert hydrated.primary.path == "primary.mp4"
    assert hydrated.portrait.path == "portrait.mp4"
    assert hydrated.landscape.path == "landscape.mp4"
    assert hydrated.primary_responsive is True
    assert hydrated.mfp_alive is False


def test_poll_vlc_returns_vlc_hydration(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass", destination=tmp_path / "windows_bridge_launch.ini")
    app_config = load_dashboard_app_config(manifest_path)

    with (
        patch("fun_time.dashboard_app.get_current_file_path", side_effect=["p.mp4", "po.mp4", "l.mp4"]),
        patch("fun_time.dashboard_app.vlc_http_req", return_value=(200, "<state>playing</state>")),
    ):
        result = poll_vlc(app_config)

    assert isinstance(result, VlcHydration)
    assert result.primary_path == "p.mp4"
    assert result.portrait_path == "po.mp4"
    assert result.landscape_path == "l.mp4"
    assert result.primary_responsive is True


def test_poll_vlc_marks_unresponsive_on_vlc_failure(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass", destination=tmp_path / "windows_bridge_launch.ini")
    app_config = load_dashboard_app_config(manifest_path)

    with (
        patch("fun_time.dashboard_app.get_current_file_path", return_value=""),
        patch("fun_time.dashboard_app.vlc_http_req", return_value=(0, "")),
    ):
        result = poll_vlc(app_config)

    assert result.primary_responsive is False
    assert result.primary_path == ""


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


def test_dashboard_scene_quit_and_omnipause_buttons_are_inside_status_strip(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )

    strip = preview_layout.main_status_strip
    quit_b = preview_layout.quit_button
    omni_b = preview_layout.omnipause_button

    # Both buttons must be fully contained within the status strip
    assert quit_b.x >= strip.x
    assert quit_b.y >= strip.y
    assert quit_b.x + quit_b.width <= strip.x + strip.width
    assert quit_b.y + quit_b.height <= strip.y + strip.height
    assert omni_b.x >= strip.x
    assert omni_b.y >= strip.y
    assert omni_b.x + omni_b.width <= strip.x + strip.width
    assert omni_b.y + omni_b.height <= strip.y + strip.height
    # Quit is left of omnipause
    assert quit_b.x < omni_b.x
    # Buttons are above the chip row (broker/fmode)
    assert quit_b.y + quit_b.height <= preview_layout.broker_panel.y


def test_dashboard_scene_omnipause_button_shows_pause_icon_when_not_paused(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=False,
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


def test_dashboard_scene_vlc_panel_labels_are_top_justified(cfg_path: Path):
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
        robot_link_enabled=True,
        primary_uses_genau=False,
        osr2_mode="controlled",
        mfp_alive=False,
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


def _make_snapshot(*, robot_link_enabled: bool = True, primary_uses_genau: bool = False) -> DashboardSnapshot:
    return DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=robot_link_enabled,
        primary_uses_genau=primary_uses_genau,
        osr2_mode="auto",
        mfp_alive=False,
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


def test_dashboard_scene_cable_connected_has_midpoint_node(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(robot_link_enabled=True)

    scene = build_dashboard_scene(layout, snapshot)

    # 2 line halves meeting at midpoint, 4 ovals (2 sockets + outer node + inner dot)
    assert len(scene.lines) == 2, "Connected cable: left half + right half"
    assert len(scene.ovals) == 4, "2 sockets + midpoint outer + midpoint inner"
    assert len(scene.arcs) == 0
    assert not any(item.text in ("Robot Link", "Broken Link") for item in scene.texts)
    # Neutral gray, no color
    assert scene.lines[0].color == COLOR_CABLE
    assert scene.lines[1].color == COLOR_CABLE


def test_dashboard_scene_cable_broken_curls_apart(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(robot_link_enabled=False)

    scene = build_dashboard_scene(layout, snapshot)

    # 2 curling line fragments, 2 socket ovals, 2 half-circle arcs
    assert len(scene.lines) == 2, "Broken cable: two curling fragments"
    assert len(scene.ovals) == 2, "Only endpoint sockets (no midpoint node)"
    assert len(scene.arcs) == 2, "Half-circle remnants of broken node"
    assert scene.lines[0].color == COLOR_CABLE_DIM
    assert scene.lines[0].smooth is True, "Broken fragments should use smooth curves"


def test_dashboard_scene_cable_spans_osr2_to_primary(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(robot_link_enabled=True)

    scene = build_dashboard_scene(layout, snapshot)

    osr2_right = layout.osr2_panel.x + layout.osr2_panel.width
    primary_left = layout.primary_panel.x
    # Left half starts at OSR2, right half ends at Primary
    assert scene.lines[0].points[0][0] == osr2_right
    assert scene.lines[1].points[-1][0] == primary_left


def test_dashboard_scene_cable_no_link_rect(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(robot_link_enabled=True)

    scene = build_dashboard_scene(layout, snapshot)

    assert not any(item.rect == layout.link_toggle for item in scene.rects)


def test_dashboard_scene_cable_press_lightens_color(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(robot_link_enabled=True)

    scene_normal = build_dashboard_scene(layout, snapshot)
    scene_pressed = build_dashboard_scene(
        layout, snapshot, pressed_actions=frozenset({"link_toggle"}),
    )

    assert scene_normal.lines[0].color == COLOR_CABLE
    assert scene_pressed.lines[0].color == lighten_color(COLOR_CABLE)


def test_dashboard_scene_chips_have_hover_texts(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    hover = {rect: text for rect, text in scene.hover_texts}
    assert layout.broker_panel in hover
    assert layout.fmode_panel in hover
    assert "broker" in hover[layout.broker_panel].lower()


def test_dashboard_scene_default_cable_connected_without_snapshot(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    assert len(scene.lines) == 2, "Default (no snapshot) should show connected cable"
    assert scene.lines[0].color == COLOR_CABLE


def test_osr2_highlights_green_when_funscript_playing(cfg_path: Path):
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    primary_root = config.paths.primary_vlc_dirs[0]
    primary_root.mkdir(parents=True, exist_ok=True)
    primary_path = primary_root / "vid.mp4"
    primary_path.write_text("v", encoding="utf-8")
    script_path = Path(
        str(primary_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")
    ) / "vid.funscript"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("s", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=False,
        osr2_mode="controlled", mfp_alive=False, primary_responsive=False, omni_paused=False,
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
    primary_root = config.paths.primary_vlc_dirs[0]
    primary_root.mkdir(parents=True, exist_ok=True)
    primary_path = primary_root / "vid.mp4"
    primary_path.write_text("v", encoding="utf-8")
    script_path = Path(
        str(primary_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")
    ) / "vid.funscript"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("s", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=True,
        osr2_mode="auto", mfp_alive=False, primary_responsive=False, omni_paused=False,
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


def test_active_chips_and_locks_use_same_green_as_favs(cfg_path: Path):
    import time as _time
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text(str(_time.time()), encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=False,
        osr2_mode="controlled", mfp_alive=True, primary_responsive=True, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", True), landscape=DashboardPanelSnapshot("", True),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot, broker_heartbeat_file=heartbeat_file)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.broker_panel] == COLOR_GREEN
    assert fills[layout.portrait_lock] == COLOR_GREEN
    assert fills[layout.landscape_lock] == COLOR_GREEN


def test_mfp_and_osr2_labels_are_top_justified(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    mfp_texts = [item for item in scene.texts if item.rect == layout.mfp_panel]
    osr2_texts = [item for item in scene.texts if item.rect == layout.osr2_panel]
    assert mfp_texts[0].anchor == "n"
    assert osr2_texts[0].anchor == "n"


def test_osr2_controlled_with_funscript_shows_funscript_control(cfg_path: Path):
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    primary_root = config.paths.primary_vlc_dirs[0]
    primary_root.mkdir(parents=True, exist_ok=True)
    primary_path = primary_root / "vid.mp4"
    primary_path.write_text("v", encoding="utf-8")
    script_path = Path(
        str(primary_root).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\")
    ) / "vid.funscript"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("s", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=False,
        osr2_mode="controlled", mfp_alive=False, primary_responsive=False, omni_paused=False,
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
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=False,
        osr2_mode="controlled", mfp_alive=False, primary_responsive=False, omni_paused=False,
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
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=False,
        osr2_mode="off", mfp_alive=False, primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    texts = {item.text for item in scene.texts}
    assert "OSR2\n(off)" in texts
    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.osr2_panel] == COLOR_PANEL


def test_portrait_label_is_split_across_two_lines(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    texts = {item.text for item in scene.texts}
    assert "Portrait\nAI VLC" in texts
    assert "Portrait AI VLC" not in texts


def test_mfp_label_has_no_connection_status_text(cfg_path: Path):
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("100.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=False,
        osr2_mode="controlled", mfp_alive=True, primary_responsive=True, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot, broker_heartbeat_file=heartbeat_file)

    mfp_texts = [item for item in scene.texts if item.rect == layout.mfp_panel]
    assert len(mfp_texts) == 1
    assert mfp_texts[0].text == "MFP"


def test_omnipause_resume_button_is_not_green_when_paused(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_genau=False,
        osr2_mode="controlled", mfp_alive=False, primary_responsive=False, omni_paused=True,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.omnipause_button] == COLOR_PANEL


def test_vlc_mode_shows_vlc_buttons_not_quarter(cfg_path: Path):
    """Non-AI VLC box should show file dialog, clipper, and nudge buttons — not 1/4."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_uses_genau=False)

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    assert "open_file_dialog" in action_ids
    assert "clipper_save" in action_ids
    assert "vlc_nudge_prev" in action_ids
    assert "vlc_nudge_next" in action_ids
    assert "quarter_button" not in action_ids


def test_genau_mode_shows_quarter_not_vlc_buttons(cfg_path: Path):
    """Genau box should show 1/4 button only."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_uses_genau=True)

    scene = build_dashboard_scene(layout, snapshot)

    action_ids = [a for a, _r in scene.actions]
    assert "quarter_button" in action_ids
    assert "open_file_dialog" not in action_ids
    assert "clipper_save" not in action_ids
    assert "vlc_nudge_prev" not in action_ids
    assert "vlc_nudge_next" not in action_ids


def test_default_scene_shows_vlc_buttons(cfg_path: Path):
    """Default (no snapshot) is Non-AI VLC, so should show VLC buttons."""
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    action_ids = [a for a, _r in scene.actions]
    assert "open_file_dialog" in action_ids
    assert "quarter_button" not in action_ids


def test_vlc_buttons_text_labels(cfg_path: Path):
    """File dialog button shows folder icon, nudge shows - and +, clipper is an image."""
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_uses_genau=False)

    scene = build_dashboard_scene(layout, snapshot)

    text_at = {item.rect: item.text for item in scene.texts}
    assert text_at[layout.open_file_dialog] == "\U0001F4C2"
    assert text_at[layout.vlc_nudge_prev] == "\u2212"  # minus sign
    assert text_at[layout.vlc_nudge_next] == "+"
    # Clipper save is now an image item, not a text item
    image_rects = {item.rect for item in scene.images}
    assert layout.clipper_save in image_rects


def test_vlc_nudge_buttons_are_adjacent_not_edge_justified(cfg_path: Path):
    """Nudge buttons should be next to each other, centered — not at panel edges."""
    layout = _make_layout(cfg_path)

    gap = layout.vlc_nudge_next.x - (layout.vlc_nudge_prev.x + layout.vlc_nudge_prev.width)
    assert gap <= 8, f"Nudge buttons should be adjacent (gap={gap})"
    # Both should be roughly centered in the primary panel
    panel_cx = layout.primary_panel.x + layout.primary_panel.width // 2
    nudge_cx = (layout.vlc_nudge_prev.x + layout.vlc_nudge_next.x + layout.vlc_nudge_next.width) // 2
    assert abs(panel_cx - nudge_cx) <= 2, "Nudge pair should be centered in panel"


def test_vlc_buttons_light_up_when_pressed(cfg_path: Path):
    layout = _make_layout(cfg_path)
    snapshot = _make_snapshot(primary_uses_genau=False)

    scene_normal = build_dashboard_scene(layout, snapshot)
    scene_pressed = build_dashboard_scene(
        layout, snapshot, pressed_actions=frozenset({"open_file_dialog", "clipper_save", "vlc_nudge_prev", "vlc_nudge_next"}),
    )

    normal_fills = {item.rect: item.fill for item in scene_normal.rects}
    pressed_fills = {item.rect: item.fill for item in scene_pressed.rects}
    for rect_name in ("open_file_dialog", "clipper_save", "vlc_nudge_prev", "vlc_nudge_next"):
        rect = getattr(layout, rect_name)
        assert pressed_fills[rect] == lighten_color(COLOR_PANEL), f"{rect_name} should light up"
        assert pressed_fills[rect] != normal_fills[rect], f"{rect_name} pressed should differ from normal"


def test_nudge_buttons_above_file_dialog_clipper_below(cfg_path: Path):
    """Nudge buttons should be above the file dialog; clipper should be below."""
    layout = _make_layout(cfg_path)

    assert layout.vlc_nudge_prev.y + layout.vlc_nudge_prev.height <= layout.open_file_dialog.y
    assert layout.vlc_nudge_next.y + layout.vlc_nudge_next.height <= layout.open_file_dialog.y
    assert layout.open_file_dialog.y + layout.open_file_dialog.height <= layout.clipper_save.y


def test_nudge_buttons_clear_of_title_area(cfg_path: Path):
    """Nudge buttons (topmost VLC buttons) must not overlap the panel label."""
    layout = _make_layout(cfg_path)

    # Title is anchored at "n" — roughly the top 14px of the panel
    title_bottom = layout.primary_panel.y + 14
    assert layout.vlc_nudge_prev.y >= title_bottom, (
        f"nudge_prev.y={layout.vlc_nudge_prev.y} overlaps title_bottom={title_bottom}"
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
