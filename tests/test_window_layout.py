from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_layout import Size
from fun_time.window_layout import (
    MonitorRect,
    compute_dashboard_size,
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


def test_dashboard_sits_at_main_monitor_top_left(cfg_path: Path):
    """The dashboard and the RFB stack in the left column so every window
    is fully visible at once — nothing overlaps anything anymore."""
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
        dashboard_chrome_height=40,
    )

    assert plan.dashboard.x == 0
    assert plan.dashboard.y == 0


def test_rfb_fills_left_column_below_dashboard(cfg_path: Path):
    config = load_config(cfg_path)
    chrome = 40

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
        dashboard_chrome_height=chrome,
    )

    assert plan.random_favs_browser.x == 0
    assert plan.random_favs_browser.y == plan.dashboard.height + chrome
    assert plan.random_favs_browser.width + plan.landscape.width == 2560
    assert (
        plan.random_favs_browser.y + plan.random_favs_browser.height == 1392
    )


def test_dashboard_offset_monitor_origin_is_respected(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(100, 50, 2560, 1392),
        secondary_monitor=MonitorRect(2660, 50, 1440, 3440),
        layout_config=config.layout,
        dashboard_chrome_height=0,
    )

    assert plan.dashboard.x == 100
    assert plan.dashboard.y == 50
    assert plan.random_favs_browser.y == 50 + plan.dashboard.height


def test_dashboard_fills_its_screen_slot(cfg_path: Path):
    """The dashboard scene scales up until it hits the left-column width
    or half the monitor height — as much space as it can take while the
    RFB keeps the other half of the column."""
    config = load_config(cfg_path)
    chrome = 40
    main = MonitorRect(0, 0, 2560, 1392)

    size = compute_dashboard_size(
        main_monitor=main,
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
        dashboard_chrome_height=chrome,
    )

    landscape_w = int(2560 * config.layout.landscape_width_ratio)
    width_budget = 2560 - landscape_w
    height_budget = 1392 // 2 - chrome
    assert size.width <= width_budget
    assert size.height <= height_budget
    # It actually grew to (nearly) fill one of the budgets.
    assert size.width >= width_budget * 0.9 or size.height >= height_budget * 0.9
    assert size.width > 481  # bigger than the old fixed-scale scene
