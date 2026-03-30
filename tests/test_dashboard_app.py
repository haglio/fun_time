from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.manifest import write_windows_bridge_manifest
from fun_time.dashboard_app import (
    COLOR_ACTIVE,
    COLOR_ACTIVE_ALT,
    COLOR_CABLE,
    COLOR_CABLE_DIM,
    COLOR_DISABLED,
    COLOR_OSR2,
    COLOR_PANEL,
    COLOR_WARNING,
    ICON_LOCK,
    ICON_TRASH,
    DashboardArcItem,
    DashboardLaunchGeometry,
    DashboardLineItem,
    DashboardOvalItem,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
    build_dashboard_window,
    hydrate_dashboard_snapshot,
    lighten_color,
    load_dashboard_app_config,
    resolve_logical_monitor_sizes,
    write_dashboard_command,
)
from fun_time.dashboard_runtime import DashboardPanelSnapshot, DashboardSnapshot, DashboardWindowSnapshot
from fun_time.dashboard_layout import DashboardPreviewLayout, Size, compute_dashboard_preview_layout
from fun_time import load_config


def test_dashboard_app_loads_layout_from_controller_manifest(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass", destination=tmp_path / "windows_bridge_launch.ini")

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.layout.main_monitor == 1
    assert app_config.layout.secondary_monitor == 2
    assert app_config.layout.landscape_width_ratio == config.controller.layout.landscape_width_ratio
    assert app_config.primary_sources == "|".join(str(path) for path in config.paths.primary_vlc_dirs)
    assert app_config.favs_file == config.paths.favs_file
    assert app_config.primary_vlc_port == config.controller.primary_vlc_http_port
    assert app_config.portrait_vlc_port == config.controller.vlc2_http_port
    assert app_config.landscape_vlc_port == config.controller.vlc3_http_port
    assert app_config.vlc_password == "vlc-pass"
    assert app_config.dashboard_state_file == config.paths.state_dir / "dashboard_state.ini"
    assert app_config.dashboard_cmd_file == config.paths.state_dir / "dashboard_cmd.txt"


def test_dashboard_highlights_primary_for_ai_video_with_funscript(cfg_path: Path):
    """Primary panel should light green for an AI video with funscript — no source roots needed."""
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
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
        primary_uses_robot_hand=False,
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
    assert fills[preview_layout.primary_panel] == COLOR_ACTIVE_ALT


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
        config.controller.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    assert scene.width == preview_layout.dashboard_width
    assert scene.height == preview_layout.dashboard_height
    assert any(item.text == "Fun Time" for item in scene.texts)
    assert len(scene.lines) == 2, "Default scene should show connected cable (two halves)"


def test_dashboard_app_scene_uses_runtime_snapshot_when_available(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
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
        primary_uses_robot_hand=False,
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
    assert fills[preview_layout.primary_panel] == COLOR_ACTIVE_ALT
    assert fills[preview_layout.portrait_panel] == COLOR_ACTIVE_ALT
    assert any(action == "portrait_lock" for action, _rect in scene.actions)
    assert any(action == "link_toggle" for action, _rect in scene.actions)


def test_osr2_auto_mode_uses_pink_not_green(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("100.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=False,
        primary_uses_robot_hand=False,
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
    assert fills[preview_layout.osr2_panel] == COLOR_OSR2
    assert COLOR_OSR2 != COLOR_ACTIVE
    assert COLOR_OSR2 != COLOR_ACTIVE_ALT


def test_osr2_non_auto_uses_panel_color(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=False,
        primary_uses_robot_hand=False,
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


def test_quarter_button_uses_osr2_pink_when_robot_hand(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("100.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=False,
        primary_uses_robot_hand=True,
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
    assert fills[preview_layout.quarter_button] == COLOR_OSR2


def test_dashboard_app_writes_commands_for_click_actions(tmp_path: Path):
    command_file = tmp_path / "state" / "dashboard_cmd.txt"

    write_dashboard_command(command_file, "portrait_next")

    assert command_file.read_text(encoding="utf-8") == "portrait_next"


def test_dashboard_window_geometry_uses_snapshot_window_when_available(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )
    scene = build_dashboard_scene(preview_layout)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_robot_hand=False,
        osr2_mode="controlled",
        mfp_alive=False,
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(111, 222, 333, 444),
    )

    class FakeRoot:
        def __init__(self):
            self.geometry_value = ""

        def geometry(self, value: str):
            self.geometry_value = value

    root = FakeRoot()
    apply_dashboard_window_geometry(root, snapshot, scene)

    assert root.geometry_value == "333x444+111+222"


def test_dashboard_window_geometry_prefers_launch_geometry_when_provided(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )
    scene = build_dashboard_scene(preview_layout)

    class FakeRoot:
        def __init__(self):
            self.geometry_value = ""

        def geometry(self, value: str):
            self.geometry_value = value

    root = FakeRoot()
    apply_dashboard_window_geometry(
        root,
        None,
        scene,
        launch_geometry=DashboardLaunchGeometry(x=11, y=22, width=333, height=444),
    )

    assert root.geometry_value == "333x444+11+22"


def test_dashboard_app_marks_broker_and_mfp_disconnected_when_heartbeat_is_stale(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("0.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_robot_hand=False,
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
    assert fills[preview_layout.broker_panel] == COLOR_DISABLED
    assert fills[preview_layout.mfp_panel] == COLOR_DISABLED


def test_dashboard_window_has_standard_decorations(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass")
    app_config = load_dashboard_app_config(manifest_path)

    with patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440))):
        root = build_dashboard_window(app_config)

    try:
        assert not root.overrideredirect()
    finally:
        root.destroy()


def test_dashboard_app_hydrates_live_vlc_state(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass")
    app_config = load_dashboard_app_config(manifest_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_robot_hand=False,
        osr2_mode="controlled",
        mfp_alive=True,
        primary_responsive=False,
        omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", True),
        landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(1, 2, 3, 4),
    )

    with (
        patch("fun_time.dashboard_app.get_current_file_path", side_effect=["primary.mp4", "portrait.mp4", "landscape.mp4"]),
        patch("fun_time.dashboard_app.vlc_http_req", return_value=(200, "<state>playing</state>")),
        patch("fun_time.dashboard_app.is_process_alive", return_value=False),
    ):
        hydrated = hydrate_dashboard_snapshot(snapshot, app_config, mfp_pid=123)

    assert hydrated.primary.path == "primary.mp4"
    assert hydrated.portrait.path == "portrait.mp4"
    assert hydrated.landscape.path == "landscape.mp4"
    assert hydrated.primary_responsive is True
    assert hydrated.mfp_alive is False


def test_dashboard_scene_has_quit_and_omnipause_actions(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    action_ids = [action for action, _rect in scene.actions]
    assert "quit" in action_ids
    assert "omnipause_toggle" in action_ids


def test_dashboard_scene_quit_and_omnipause_buttons_are_above_main_monitor(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )

    assert preview_layout.quit_button.y + preview_layout.quit_button.height <= preview_layout.main_monitor.y
    assert preview_layout.omnipause_button.y + preview_layout.omnipause_button.height <= preview_layout.main_monitor.y
    assert preview_layout.quit_button.x < preview_layout.omnipause_button.x


def test_dashboard_scene_omnipause_button_shows_pause_icon_when_not_paused(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_robot_hand=False,
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
        config.controller.layout,
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
        config.controller.layout,
    )
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_robot_hand=False,
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
    assert lighten_color("#2A3038", 50) == "#5C626A"
    assert lighten_color("#000000", 30) == "#1E1E1E"


def test_lighten_color_caps_at_255():
    assert lighten_color("#F0F0F0", 50) == "#FFFFFF"


def test_dashboard_scene_lock_buttons_use_icon(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
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
        config.controller.layout,
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
        config.controller.layout,
    )

    scene = build_dashboard_scene(preview_layout)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[preview_layout.portrait_trash] != COLOR_WARNING
    assert fills[preview_layout.landscape_trash] != COLOR_WARNING
    assert fills[preview_layout.portrait_trash] == COLOR_PANEL
    assert fills[preview_layout.landscape_trash] == COLOR_PANEL


def test_dashboard_scene_pressed_button_has_lighter_fill(cfg_path: Path):
    config = load_config(cfg_path)
    preview_layout = compute_dashboard_preview_layout(
        Size(2560, 1392),
        Size(1440, 3440),
        config.controller.layout,
    )

    scene_normal = build_dashboard_scene(preview_layout)
    scene_pressed = build_dashboard_scene(preview_layout, pressed_actions=frozenset({"portrait_prev"}))

    normal_fills = {item.rect: item.fill for item in scene_normal.rects}
    pressed_fills = {item.rect: item.fill for item in scene_pressed.rects}
    assert pressed_fills[preview_layout.portrait_prev] != normal_fills[preview_layout.portrait_prev]
    assert pressed_fills[preview_layout.portrait_prev] == lighten_color(COLOR_PANEL)
    # Non-pressed buttons should keep their normal fill
    assert pressed_fills[preview_layout.portrait_next] == normal_fills[preview_layout.portrait_next]


def _make_snapshot(*, robot_link_enabled: bool = True, primary_uses_robot_hand: bool = False) -> DashboardSnapshot:
    return DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=robot_link_enabled,
        primary_uses_robot_hand=primary_uses_robot_hand,
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
        config.controller.layout,
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
        Size(2560, 1392), Size(1440, 3440), config.controller.layout,
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
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_robot_hand=False,
        osr2_mode="controlled", mfp_alive=False, primary_responsive=False, omni_paused=False,
        primary=DashboardPanelSnapshot(str(primary_path), False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.primary_panel] == COLOR_ACTIVE_ALT
    assert fills[layout.osr2_panel] == COLOR_ACTIVE_ALT


def test_robot_hand_label_says_robot_hand():
    from fun_time.dashboard_state import LABEL_PRIMARY_ROBOT
    assert LABEL_PRIMARY_ROBOT == "Robot Hand"


def test_quit_button_uses_neutral_grey(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.quit_button] == COLOR_PANEL


def test_active_chips_and_locks_use_same_green_as_favs(cfg_path: Path):
    import time as _time
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.controller.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text(str(_time.time()), encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_robot_hand=False,
        osr2_mode="controlled", mfp_alive=True, primary_responsive=True, omni_paused=False,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", True), landscape=DashboardPanelSnapshot("", True),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot, broker_heartbeat_file=heartbeat_file)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.broker_panel] == COLOR_ACTIVE_ALT
    assert fills[layout.portrait_lock] == COLOR_ACTIVE_ALT
    assert fills[layout.landscape_lock] == COLOR_ACTIVE_ALT


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
        Size(2560, 1392), Size(1440, 3440), config.controller.layout,
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
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_robot_hand=False,
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
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_robot_hand=False,
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


def test_portrait_label_is_split_across_two_lines(cfg_path: Path):
    layout = _make_layout(cfg_path)

    scene = build_dashboard_scene(layout)

    texts = {item.text for item in scene.texts}
    assert "Portrait\nAI VLC" in texts
    assert "Portrait AI VLC" not in texts


def test_mfp_label_has_no_connection_status_text(cfg_path: Path):
    config = load_config(cfg_path)
    layout = compute_dashboard_preview_layout(
        Size(2560, 1392), Size(1440, 3440), config.controller.layout,
    )
    heartbeat_file = config.paths.state_dir / "broker_heartbeat.txt"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file.write_text("100.0", encoding="utf-8")
    snapshot = DashboardSnapshot(
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_robot_hand=False,
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
        f_mode_enabled=False, robot_link_enabled=True, primary_uses_robot_hand=False,
        osr2_mode="controlled", mfp_alive=False, primary_responsive=False, omni_paused=True,
        primary=DashboardPanelSnapshot("", False),
        portrait=DashboardPanelSnapshot("", False), landscape=DashboardPanelSnapshot("", False),
        window=DashboardWindowSnapshot(0, 0, 0, 0),
    )

    scene = build_dashboard_scene(layout, snapshot)

    fills = {item.rect: item.fill for item in scene.rects}
    assert fills[layout.omnipause_button] == COLOR_PANEL
