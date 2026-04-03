from __future__ import annotations

from pathlib import Path

from fun_time.window_layout import (
    MonitorRect,
    Size,
    compute_dashboard_size,
    compute_left_partition_stack,
    compute_window_layout,
)
from fun_time import load_config
from fun_time.config import LayoutConfig


def test_compute_window_layout_uses_secondary_monitor_for_portrait_primary_and_genau(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
        mfp_size=Size(240, 395),
    )

    assert plan.portrait.x == 2560
    assert plan.portrait.y == 0
    assert plan.portrait.width == 1440
    assert plan.primary.x == 2560
    assert plan.primary.y == plan.portrait.height
    assert plan.genau == plan.primary


def test_compute_window_layout_uses_main_monitor_for_landscape_and_random_favs_browser(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
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
        layout_config=config.layout,
        dashboard_size=Size(321, 266),
        mfp_size=Size(240, 395),
    )

    assert dashboard.x == 267
    assert dashboard.y == 365
    assert mfp.x == 308
    assert mfp.y == 996


def test_compute_left_partition_stack_equal_visual_gaps_with_chrome(cfg_path: Path):
    config = load_config(cfg_path)
    chrome_h = 31  # typical Windows title bar + border

    dashboard, mfp = compute_left_partition_stack(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        layout_config=config.layout,
        dashboard_size=Size(321, 266),
        mfp_size=Size(240, 395),
        dashboard_chrome_height=chrome_h,
    )

    gap_top = dashboard.y
    gap_mid = mfp.y - (dashboard.y + dashboard.height + chrome_h)
    assert abs(gap_top - gap_mid) <= 1


def test_compute_left_partition_stack_respects_top_margin(cfg_path: Path):
    config = load_config(cfg_path)
    chrome_h = 31
    # Use a top margin that reserves ~8% of screen for browser chrome
    cfg = config.layout
    margin_cfg = LayoutConfig(
        main_monitor=cfg.main_monitor,
        secondary_monitor=cfg.secondary_monitor,
        primary_top_ratio=cfg.primary_top_ratio,
        landscape_width_ratio=cfg.landscape_width_ratio,
        mfp_width_ratio=cfg.mfp_width_ratio,
        mfp_height_ratio=cfg.mfp_height_ratio,
        left_partition_top_ratio=0.08,
    )
    dashboard, mfp = compute_left_partition_stack(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        layout_config=margin_cfg,
        dashboard_size=Size(321, 266),
        mfp_size=Size(240, 395),
        dashboard_chrome_height=chrome_h,
    )
    top_margin = int(1392 * 0.08)  # 111
    # Dashboard must start below the top margin
    assert dashboard.y >= top_margin
    # Top and middle gaps should be equal; MFP sits near the bottom
    gap_top = dashboard.y - top_margin
    gap_mid = mfp.y - (dashboard.y + dashboard.height + chrome_h)
    assert abs(gap_top - gap_mid) <= 1


def test_compute_dashboard_size_matches_preview_layout(cfg_path: Path):
    config = load_config(cfg_path)

    size = compute_dashboard_size(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert size == Size(321, 266)
