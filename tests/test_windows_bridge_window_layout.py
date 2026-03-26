from __future__ import annotations

from pathlib import Path

from fun_time.windows_bridge_window_layout import (
    MonitorRect,
    Size,
    compute_dashboard_size,
    compute_left_partition_stack,
    compute_window_layout,
    plan_window_layout,
    write_window_layout_plan,
)
from fun_time.windows_bridge_monitors import MonitorInfo
from fun_time import load_config


def test_compute_window_layout_uses_secondary_monitor_for_portrait_primary_and_robot_hand(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.controller.layout,
        mfp_size=Size(240, 395),
    )

    assert plan.portrait.x == 2560
    assert plan.portrait.y == 0
    assert plan.portrait.width == 1440
    assert plan.primary.x == 2560
    assert plan.primary.y == plan.portrait.height
    assert plan.robot_hand == plan.primary


def test_compute_window_layout_uses_main_monitor_for_landscape_and_random_favs_browser(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.controller.layout,
        mfp_size=Size(240, 395),
    )

    assert plan.landscape.height == 1392
    assert plan.landscape.x > 0
    assert plan.random_favs_browser.x == 0
    assert plan.random_favs_browser.width + plan.landscape.width == 2560


def test_compute_left_partition_stack_centers_dashboard_above_mfp(cfg_path: Path):
    config = load_config(cfg_path)

    dashboard, mfp = compute_left_partition_stack(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        layout_config=config.controller.layout,
        dashboard_size=Size(321, 266),
        mfp_size=Size(240, 395),
    )

    assert dashboard.x == 267
    assert dashboard.y == 243
    assert mfp.x == 308
    assert mfp.y == 752


def test_compute_dashboard_size_matches_preview_layout(cfg_path: Path):
    config = load_config(cfg_path)

    size = compute_dashboard_size(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.controller.layout,
    )

    assert size == Size(321, 266)


def test_write_window_layout_plan_writes_named_sections(tmp_path: Path, cfg_path: Path):
    config = load_config(cfg_path)
    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.controller.layout,
        mfp_size=Size(240, 395),
    )
    plan_path = tmp_path / "window_layout_plan.ini"

    write_window_layout_plan(plan_path, plan)

    text = plan_path.read_text(encoding="utf-8")
    assert "[portrait]" in text
    assert "[dashboard]" in text
    assert "width = 321" in text


def test_plan_window_layout_combines_monitors_and_config(cfg_path: Path):
    config = load_config(cfg_path)
    monitors = [
        MonitorInfo(x=0, y=0, width=2560, height=1392),
        MonitorInfo(x=2560, y=0, width=1440, height=3440),
    ]

    plan = plan_window_layout(
        monitors=monitors,
        layout_config=config.controller.layout,
        mfp_size=Size(240, 395),
    )

    assert plan.portrait.x == 2560
    assert plan.landscape.width == int(2560 * config.controller.layout.landscape_width_ratio)
    assert plan.mfp.width == 240
