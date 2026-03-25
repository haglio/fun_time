from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.controller_manifest import write_controller_manifest
from fun_time.dashboard_app import (
    COLOR_DISABLED,
    COLOR_ACTIVE_ALT,
    DashboardLaunchGeometry,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
    hydrate_dashboard_snapshot,
    load_dashboard_app_config,
    resolve_logical_monitor_sizes,
    write_dashboard_command,
)
from fun_time.dashboard_runtime import DashboardPanelSnapshot, DashboardSnapshot, DashboardWindowSnapshot
from fun_time.dashboard_layout import Size, compute_dashboard_preview_layout
from fun_time import load_config


def test_dashboard_app_loads_layout_from_controller_manifest(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_controller_manifest(config, "vlc-pass", destination=tmp_path / "controller_launch.ini")

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
    assert any(item.text == "Robot Link" for item in scene.texts)


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
        primary=DashboardPanelSnapshot(str(primary_path), False),
        portrait=DashboardPanelSnapshot(str(portrait_path), True),
        landscape=DashboardPanelSnapshot(str(landscape_path), False),
        window=DashboardWindowSnapshot(10, 20, 300, 200),
    )

    scene = build_dashboard_scene(
        preview_layout,
        snapshot,
        primary_sources="|".join(str(path) for path in config.paths.primary_vlc_dirs),
        favs_file=favs_file,
        broker_heartbeat_file=heartbeat_file,
    )

    texts = {item.text for item in scene.texts}
    fills = {item.rect: item.fill for item in scene.rects}
    assert "Broken Link" in texts
    assert "Non-AI VLC\nprimary.mp4" in texts
    assert "Portrait AI VLC\nportrait.mp4" in texts
    assert fills[preview_layout.primary_panel] == COLOR_ACTIVE_ALT
    assert fills[preview_layout.portrait_panel] == COLOR_ACTIVE_ALT
    assert any(action == "portrait_lock" for action, _rect in scene.actions)
    assert any(action == "link_toggle" for action, _rect in scene.actions)


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


def test_dashboard_app_hydrates_live_vlc_state(cfg_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_controller_manifest(config, "vlc-pass")
    app_config = load_dashboard_app_config(manifest_path)
    snapshot = DashboardSnapshot(
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_robot_hand=False,
        osr2_mode="controlled",
        mfp_alive=True,
        primary_responsive=False,
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
