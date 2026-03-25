from __future__ import annotations

from pathlib import Path

from fun_time.controller_manifest import write_controller_manifest
from fun_time.dashboard_app import (
    COLOR_ACTIVE_ALT,
    apply_dashboard_window_geometry,
    build_dashboard_scene,
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
    snapshot = DashboardSnapshot(
        broker_running=True,
        controller_running=True,
        f_mode_enabled=False,
        robot_link_enabled=False,
        primary_uses_robot_hand=False,
        osr2_mode="auto",
        mfp_connected=True,
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
        broker_running=False,
        controller_running=True,
        f_mode_enabled=False,
        robot_link_enabled=True,
        primary_uses_robot_hand=False,
        osr2_mode="controlled",
        mfp_connected=False,
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
