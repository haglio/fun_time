from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_layout import Size, compute_dashboard_preview_layout
from fun_time.window_layout import (
    MonitorRect,
    compute_dashboard_size,
    compute_primary_media_rect,
    compute_window_layout,
)
from fun_time import load_config


def test_compute_window_layout_uses_secondary_monitor_for_portrait(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.portrait.x == 2560
    assert plan.portrait.y == 0
    assert plan.portrait.width == 1440


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


def test_dashboard_sits_at_the_top_left_corner_of_the_left_column(cfg_path: Path):
    """The dashboard and the RFB stack in the left column so every window
    is fully visible at once.  The dashboard is flush against the column's
    top-left corner."""
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.dashboard.x == 0
    assert plan.dashboard.y == 0


def test_dashboard_spans_the_whole_left_column_above_the_rfb(cfg_path: Path):
    """The dashboard now embeds the log stream, so its window spans the full
    left-column width — schematic on the left, log strip filling the rest —
    rather than leaving room for a second log-panel window beside it."""
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    left_width = plan.random_favs_browser.width
    assert plan.dashboard.x == 0
    assert plan.dashboard.width == left_width


def test_rfb_fills_the_rectangle_below_the_dashboard(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
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
        main_monitor=MonitorRect(100, 50, 2560, 1392),
        secondary_monitor=MonitorRect(2660, 50, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.dashboard.x == 100
    assert plan.dashboard.y == 50
    assert plan.random_favs_browser.x == 100
    assert plan.random_favs_browser.y == 50 + plan.dashboard.height


def test_primary_media_rect_is_the_secondary_below_the_portrait_satellite(cfg_path: Path):
    """The primary player fills the secondary monitor below the portrait's slice
    — the rect startup launches Nau/Genau into and the notice overlay flashes
    primary notices over.  It abuts the portrait window with no gap and no
    overlap, and reaches the monitor's bottom."""
    config = load_config(cfg_path)
    secondary = MonitorRect(2560, 0, 1440, 3440)

    plan = compute_window_layout(
        main_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=secondary,
        layout_config=config.layout,
    )
    primary = compute_primary_media_rect(secondary_monitor=secondary, layout_config=config.layout)

    assert primary.x == secondary.x
    assert primary.width == secondary.width
    # Starts exactly where the portrait satellite ends, and runs to the bottom.
    assert primary.y == plan.portrait.y + plan.portrait.height
    assert primary.y + primary.height == secondary.y + secondary.height


def test_dashboard_scene_keeps_its_natural_size(cfg_path: Path):
    """``compute_dashboard_size`` returns the schematic's natural (fixed-scale)
    scene size — the shape the schematic has always had.  The window is wider
    than this (it spans the column, with the log stream filling the remainder),
    so the schematic must be strictly narrower than the column."""
    config = load_config(cfg_path)
    main = MonitorRect(0, 0, 2560, 1392)
    secondary = MonitorRect(2560, 0, 1440, 3440)

    size = compute_dashboard_size(
        main_monitor=main,
        secondary_monitor=secondary,
        layout_config=config.layout,
    )

    natural = compute_dashboard_preview_layout(
        Size(main.width, main.height),
        Size(secondary.width, secondary.height),
        config.layout,
    )
    assert size.width == natural.dashboard_width
    assert size.height == natural.dashboard_height

    # The schematic is narrower than the column, leaving room for the log strip.
    landscape_w = int(2560 * config.layout.landscape_width_ratio)
    left_width = 2560 - landscape_w
    assert size.width < left_width
