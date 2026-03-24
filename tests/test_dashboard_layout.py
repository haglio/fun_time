from __future__ import annotations

from fun_time.config import LayoutConfig
from fun_time.dashboard_layout import Size, compute_dashboard_preview_layout


def _layout_config() -> LayoutConfig:
    return LayoutConfig(
        main_monitor=1,
        secondary_monitor=2,
        primary_top_ratio=0.7272727273,
        landscape_width_ratio=0.6666666667,
        mfp_width_ratio=0.9,
        mfp_height_ratio=0.6,
    )


def test_dashboard_preview_layout_uses_monitor_proportions_directly():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.main_monitor.width == 186
    assert layout.main_monitor.height == 101
    assert layout.secondary_monitor.width == 105
    assert layout.secondary_monitor.height == 250


def test_dashboard_preview_centers_left_column_controls_within_main_monitor():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    left_space_before_mfp = layout.mfp_panel.x - layout.main_monitor.x
    left_space_before_strip = layout.main_status_strip.x - layout.main_monitor.x

    assert left_space_before_mfp > 0
    assert left_space_before_strip > 0
    assert layout.main_status_strip.width >= layout.mfp_panel.width


def test_dashboard_preview_places_osr2_and_link_in_gap_left_of_secondary_stack():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.osr2_panel.x + layout.osr2_panel.width < layout.link_toggle.x
    assert layout.link_toggle.x + layout.link_toggle.width < layout.secondary_monitor.x
