from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_layout import Size
from fun_time.window_layout import (
    MonitorRect,
    compute_dashboard_size,
    compute_left_partition_dashboard,
    compute_window_layout,
)
from fun_time import load_config
from fun_time.config import LayoutConfig


def test_compute_window_layout_uses_secondary_monitor_for_portrait_and_primary_players(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.portrait.x == 2560
    assert plan.portrait.y == 0
    assert plan.portrait.width == 1440
    assert plan.primary.x == 2560
    assert plan.primary.y == plan.portrait.height


def test_compute_window_layout_uses_main_monitor_for_landscape_and_random_favs_browser(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.landscape.height == 1392
    assert plan.landscape.x > 0
    assert plan.random_favs_browser.x == 0
    assert plan.random_favs_browser.width + plan.landscape.width == 2560


def test_compute_left_partition_dashboard_centers_dashboard(cfg_path: Path):
    config = load_config(cfg_path)

    dashboard = compute_left_partition_dashboard(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        layout_config=config.layout,
        dashboard_size=Size(321, 266),
    )

    # Left partition is main width minus the landscape strip: 2560 - 1704 = 856.
    assert dashboard.x == (856 - 321) // 2
    assert dashboard.y == (1392 - 266) // 2
    assert dashboard.width == 321
    assert dashboard.height == 266


def test_compute_left_partition_dashboard_equal_visual_gaps_with_chrome(cfg_path: Path):
    config = load_config(cfg_path)
    chrome_h = 31  # typical Windows title bar + border

    dashboard = compute_left_partition_dashboard(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        layout_config=config.layout,
        dashboard_size=Size(321, 266),
        dashboard_chrome_height=chrome_h,
    )

    gap_top = dashboard.y
    gap_bottom = 1392 - (dashboard.y + dashboard.height + chrome_h)
    assert abs(gap_top - gap_bottom) <= 1


def test_compute_left_partition_dashboard_respects_margins(cfg_path: Path):
    config = load_config(cfg_path)
    chrome_h = 31
    cfg = config.layout
    margin_cfg = LayoutConfig(
        main_monitor=cfg.main_monitor,
        secondary_monitor=cfg.secondary_monitor,
        primary_top_ratio=cfg.primary_top_ratio,
        landscape_width_ratio=cfg.landscape_width_ratio,
        left_partition_top_ratio=0.08,
        left_partition_bottom_ratio=0.05,
    )

    dashboard = compute_left_partition_dashboard(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        layout_config=margin_cfg,
        dashboard_size=Size(321, 266),
        dashboard_chrome_height=chrome_h,
    )

    top_margin = int(1392 * 0.08)  # 111
    bottom_margin = int(1392 * 0.05)  # 69
    # Dashboard must start below the top margin
    assert dashboard.y >= top_margin
    # Gaps between the margins and the dashboard should be equal
    gap_top = dashboard.y - top_margin
    gap_bottom = (1392 - bottom_margin) - (dashboard.y + dashboard.height + chrome_h)
    assert abs(gap_top - gap_bottom) <= 1


def test_compute_window_layout_dashboard_matches_left_partition_helper(cfg_path: Path):
    config = load_config(cfg_path)
    main = MonitorRect(0, 0, 2560, 1392)
    secondary = MonitorRect(2560, 0, 1440, 3440)

    plan = compute_window_layout(
        main_monitor=main,
        secondary_monitor=secondary,
        layout_config=config.layout,
        dashboard_chrome_height=31,
    )
    expected = compute_left_partition_dashboard(
        main_monitor=main,
        layout_config=config.layout,
        dashboard_size=compute_dashboard_size(
            main_monitor=main, secondary_monitor=secondary, layout_config=config.layout,
        ),
        dashboard_chrome_height=31,
    )

    assert plan.dashboard == expected


def test_compute_dashboard_size_matches_preview_layout(cfg_path: Path):
    config = load_config(cfg_path)

    size = compute_dashboard_size(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert size == Size(481, 399)
