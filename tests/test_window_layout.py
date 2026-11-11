from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time import load_config
from fun_time.crown import Crown
from fun_time.dashboard_layout import RFB_MODE_SELECTOR_HEIGHT, dashboard_window_height
from fun_time.monitors import MonitorInfo
from fun_time.window_layout import (
    MonitorRect,
    compute_main_media_rect,
    compute_window_layout,
    screen_layout,
    secondary_monitor_rects,
)
from fun_time.window_names import FIXED_TOPMOST_WINDOWS


def test_screen_layout_plans_for_the_monitors_this_machine_has(cfg_path: Path):
    config = load_config(cfg_path)
    monitors = [MonitorInfo(0, 0, 2560, 1392), MonitorInfo(2560, 0, 1440, 3440)]

    with patch("fun_time.window_layout.enumerate_monitors", return_value=monitors):
        layout = screen_layout(config.layout)

    assert layout.plan == compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )
    assert layout.secondary_monitor == MonitorRect(2560, 0, 1440, 3440)
    assert layout.config == config.layout


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


def test_the_primary_monitor_holds_the_landscape_satellite_and_the_browser(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.landscape.height == 1392
    assert plan.landscape.x > 0
    assert plan.random_faves_browser.x == 0
    assert plan.random_faves_browser.width + plan.landscape.width == 2560


def test_dashboard_sits_at_the_top_left_corner_of_the_left_column(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.dashboard.x == 0
    assert plan.dashboard.y == 0


def test_dashboard_spans_the_whole_left_column_above_the_rfb(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    left_width = plan.random_faves_browser.width
    assert plan.dashboard.x == 0
    assert plan.dashboard.width == left_width


def test_rfb_fills_the_rectangle_below_the_dashboard(cfg_path: Path):
    config = load_config(cfg_path)

    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.random_faves_browser.x == 0
    assert plan.random_faves_browser.y == plan.dashboard.y + plan.dashboard.height
    assert plan.random_faves_browser.width + plan.landscape.width == 2560
    assert (
        plan.random_faves_browser.y + plan.random_faves_browser.height == 1392
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
    assert plan.random_faves_browser.x == 100
    assert plan.random_faves_browser.y == 50 + plan.dashboard.height


def test_the_main_funestras_rect_is_on_the_secondary_below_the_portrait_satellite(cfg_path: Path):
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
    assert main.y == plan.portrait.y + plan.portrait.height
    assert main.y + main.height == secondary.y + secondary.height


def test_the_dashboard_takes_a_bar_and_a_log_and_the_browser_takes_the_rest(cfg_path: Path):
    config = load_config(cfg_path)
    main = MonitorRect(0, 0, 2560, 1392)

    plan = compute_window_layout(
        primary_monitor=main,
        secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
        layout_config=config.layout,
    )

    assert plan.dashboard.height == dashboard_window_height()
    assert plan.dashboard.width == plan.random_faves_browser.width
    assert plan.random_faves_browser.y == plan.dashboard.y + plan.dashboard.height
    assert plan.random_faves_browser.height > plan.dashboard.height


def test_the_strip_under_the_log_takes_its_height_from_the_browsers_place(cfg_path: Path):
    def plan(**strip):
        return compute_window_layout(
            primary_monitor=MonitorRect(0, 0, 2560, 1392),
            secondary_monitor=MonitorRect(2560, 0, 1440, 3440),
            layout_config=load_config(cfg_path).layout, **strip)

    without, with_mode_selector = plan(), plan(rfb_mode_selector=True)

    taller = with_mode_selector.dashboard.height - without.dashboard.height
    assert taller == RFB_MODE_SELECTOR_HEIGHT
    assert without.random_faves_browser.height - with_mode_selector.random_faves_browser.height == taller
    assert with_mode_selector.random_faves_browser.y == (
        with_mode_selector.dashboard.y + with_mode_selector.dashboard.height)
    assert with_mode_selector.dashboard.height == dashboard_window_height(rfb_mode_selector=True)


def test_a_main_funestra_given_most_of_the_screen_trades_sizes_with_the_portrait_funestra(
        cfg_path: Path):
    layout = load_config(cfg_path).layout
    secondary = MonitorRect(2560, 0, 1440, 3440)
    usual_portrait = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392), secondary_monitor=secondary,
        layout_config=layout).portrait
    usual_main = compute_main_media_rect(secondary_monitor=secondary, layout_config=layout)

    rects = secondary_monitor_rects(secondary, layout, majority=Crown.MAIN)

    assert rects.portrait == MonitorRect(2560, 0, usual_main.width, usual_main.height)
    assert rects.main == MonitorRect(
        2560, 3440 - usual_portrait.height, usual_portrait.width, usual_portrait.height)


def test_a_portrait_funestra_given_most_of_the_screen_sits_where_startup_puts_both(cfg_path: Path):
    layout = load_config(cfg_path).layout
    secondary = MonitorRect(2560, 0, 1440, 3440)

    rects = secondary_monitor_rects(secondary, layout, majority=Crown.PORTRAIT)

    assert rects.portrait == compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392), secondary_monitor=secondary,
        layout_config=layout).portrait
    assert rects.main == compute_main_media_rect(secondary_monitor=secondary, layout_config=layout)


def _overlap(one, other) -> bool:
    return (one.x < other.x + other.width and other.x < one.x + one.width
            and one.y < other.y + other.height and other.y < one.y + one.height)


def _overlapping(rects) -> list[tuple[str, str]]:
    return [(one, other) for one in rects for other in rects
            if one < other and _overlap(rects[one], rects[other])]


def _laid_out_on_two_monitors(cfg_path: Path):
    layout_config = load_config(cfg_path).layout
    secondary = MonitorRect(2560, 0, 1440, 3440)
    plan = compute_window_layout(
        primary_monitor=MonitorRect(0, 0, 2560, 1392),
        secondary_monitor=secondary,
        layout_config=layout_config,
    )
    return layout_config, secondary, plan


def test_the_always_topmost_windows_never_overlap_whatever_their_order(cfg_path: Path):
    layout_config, secondary, plan = _laid_out_on_two_monitors(cfg_path)

    for majority in Crown:
        on_the_secondary = secondary_monitor_rects(secondary, layout_config, majority=majority)
        rects = {
            "portrait": on_the_secondary.portrait,
            "landscape": plan.landscape,
            "dashboard": plan.dashboard,
            "main_funestra": on_the_secondary.main,
        }
        assert set(rects) == set(FIXED_TOPMOST_WINDOWS)
        assert _overlapping(rects) == [], majority


def test_every_window_is_fully_visible_at_once(cfg_path: Path):
    layout_config, secondary, plan = _laid_out_on_two_monitors(cfg_path)

    assert _overlapping({
        "portrait": plan.portrait,
        "landscape": plan.landscape,
        "dashboard": plan.dashboard,
        "rfb": plan.random_faves_browser,
        "main_funestra": compute_main_media_rect(
            secondary_monitor=secondary, layout_config=layout_config),
    }) == []
