from __future__ import annotations

from pathlib import Path

from fun_time.controller_manifest import write_controller_manifest
from fun_time.dashboard_app import (
    build_dashboard_scene,
    load_dashboard_app_config,
    resolve_logical_monitor_sizes,
)
from fun_time.dashboard_runtime import DashboardPanelSnapshot, DashboardSnapshot
from fun_time.dashboard_layout import Size, compute_dashboard_preview_layout
from fun_time import load_config


def test_dashboard_app_loads_layout_from_controller_manifest(cfg_path: Path, tmp_path: Path):
    config = load_config(cfg_path)
    manifest_path = write_controller_manifest(config, "vlc-pass", destination=tmp_path / "controller_launch.ini")

    app_config = load_dashboard_app_config(manifest_path)

    assert app_config.layout.main_monitor == 1
    assert app_config.layout.secondary_monitor == 2
    assert app_config.layout.landscape_width_ratio == config.controller.layout.landscape_width_ratio
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
    snapshot = DashboardSnapshot(
        broker_running=True,
        controller_running=True,
        f_mode_enabled=False,
        robot_link_enabled=False,
        osr2_mode="auto",
        mfp_connected=True,
        primary=DashboardPanelSnapshot("Non-AI VLC", "primary.mp4", True, ""),
        portrait=DashboardPanelSnapshot("Portrait AI VLC", "portrait.mp4", False, ""),
        landscape=DashboardPanelSnapshot("Landscape AI VLC", "landscape.mp4", True, ""),
    )

    scene = build_dashboard_scene(preview_layout, snapshot)

    texts = {item.text for item in scene.texts}
    assert "Broken Link" in texts
    assert "Non-AI VLC\nprimary.mp4" in texts
    assert "Portrait AI VLC\nportrait.mp4" in texts
