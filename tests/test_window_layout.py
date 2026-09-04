from __future__ import annotations

from pathlib import Path

from fun_time import load_config
from fun_time.dashboard_layout import dashboard_window_height
from fun_time.window_layout import (
    MonitorRect,
    compute_main_media_rect,
    compute_window_layout,
)


def test_compute_window_layout_uses_secondary_monitor_for_portrait(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.portrait.x == 2560
    assert plan.portrait.y == 0
    assert plan.portrait.width == 1440


def test_compute_window_layout_uses_main_monitor_for_landscape_and_random_favs_browser(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.landscape.height == 1392
    assert plan.landscape.x > 0
    assert plan.random_favs_browser.x == 0
    assert plan.random_favs_browser.width + plan.landscape.width == 2560


def test_dashboard_sits_at_the_top_left_corner_of_the_left_column(cfg_path: Path):
    """The dashboard and the RFB stack in the left column so every window
    is fully visible at once.  The dashboard is flush against the column's
    top-left corner."""
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.dashboard.x == 0
    assert plan.dashboard.y == 0


def test_dashboard_spans_the_whole_left_column_above_the_rfb(cfg_path: Path):
    """The dashboard now embeds the log stream, so its window spans the full
    left-column width — the control bar across the top, the log filling the rest —
    rather than leaving room for a second log-panel window beside it."""
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    left_width = plan.random_favs_browser.width
    assert plan.dashboard.x == 0
    assert plan.dashboard.width == left_width


def test_rfb_fills_the_rectangle_below_the_dashboard(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.random_favs_browser.x == 0
    # The RFB starts exactly at the dashboard's bottom (no gap) ...
    assert plan.random_favs_browser.y == plan.dashboard.y + plan.dashboard.height
    # ... spans the full left-column width ...
    assert plan.random_favs_browser.width + plan.landscape.width == 2560
    # ... and reaches down to the monitor's bottom edge.
    assert (
        plan.random_favs_browser.y + plan.random_favs_browser.height == 1392
    )


def test_dashboard_offset_monitor_origin_is_respected(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(100, 50, 2560, 1392),
        secondary_monitor=MonitorRect(2660, 50, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.dashboard.x == 100
    assert plan.dashboard.y == 50
    assert plan.random_favs_browser.x == 100
    assert plan.random_favs_browser.y == 50 + plan.dashboard.height


def test_primary_media_rect_is_the_secondary_below_the_portrait_satellite(cfg_path: Path):
    """The main player fills the secondary monitor below the portrait's slice
    — the rect startup launches Nau/Genau into and the notice overlay flashes
    main-player notices over.  It abuts the portrait window with no gap and no
    overlap, and reaches the monitor's bottom."""
    config = load_config(cfg_path)
    secondary = MonitorRect(2560, 0, 1440, 3440)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=secondary,
        layout_config=config.layout,
    )
    main = compute_main_media_rect(secondary_monitor=secondary, layout_config=config.layout)

    assert main.x == secondary.x
    assert main.width == secondary.width
    # Starts exactly where the portrait satellite ends, and runs to the bottom.
    assert main.y == plan.portrait.y + plan.portrait.height
    assert main.y + main.height == secondary.y + secondary.height


def test_the_dashboard_takes_a_bar_and_a_log_and_the_browser_takes_the_rest(cfg_path: Path):
    """The dashboard used to stand as tall as a scale drawing of the taller
    monitor.  It is a control bar over a log now, so the browser under it gets
    the height the drawing was using."""
    config = load_config(cfg_path)
    main = MonitorRect(0, 0, 2560, 1392)

    plan = compute_window_layout(
        primary_monitor=main,
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.dashboard.height == dashboard_window_height()
    # It spans the whole left column.
    assert plan.dashboard.width == plan.random_favs_browser.width
    assert plan.random_favs_browser.y == plan.dashboard.y + plan.dashboard.height
    assert plan.random_favs_browser.height > plan.dashboard.height

