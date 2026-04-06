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

    assert layout.main_monitor.width == 279
    assert layout.main_monitor.height == 152
    assert layout.secondary_monitor.width == 157
    assert layout.secondary_monitor.height == 375


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


def test_dashboard_preview_places_osr2_left_of_secondary_stack():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.osr2_panel.x + layout.osr2_panel.width < layout.secondary_monitor.x


def test_genau_mode_toggle_inside_primary_panel():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    btn = layout.genau_mode_toggle
    panel = layout.primary_panel
    assert btn.x >= panel.x
    assert btn.y >= panel.y
    assert btn.x + btn.width <= panel.x + panel.width
    assert btn.y + btn.height <= panel.y + panel.height


def test_osr2_box_is_at_least_66_pixels():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.osr2_panel.width >= 66
    assert layout.osr2_panel.height >= 66


def test_broker_fmode_voice_chips_match_button_size():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.broker_panel.width == layout.quit_button.width
    assert layout.broker_panel.height == layout.quit_button.height
    assert layout.fmode_panel.width == layout.quit_button.width
    assert layout.fmode_panel.height == layout.quit_button.height
    assert layout.voice_panel.width == layout.quit_button.width
    assert layout.voice_panel.height == layout.quit_button.height


def test_status_strip_side_margin_matches_top_margin():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    strip = layout.main_status_strip
    btn = layout.quit_button
    side_margin = btn.x - strip.x
    top_margin = btn.y - strip.y
    assert side_margin == top_margin


def test_rfb_box_encloses_status_strip_and_mfp():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    rfb = layout.rfb_panel
    strip = layout.main_status_strip
    mfp = layout.mfp_panel

    # RFB box must fully enclose both sub-panels
    assert rfb.x < strip.x
    assert rfb.y < strip.y
    assert rfb.x + rfb.width > strip.x + strip.width
    assert rfb.x < mfp.x
    assert rfb.x + rfb.width > mfp.x + mfp.width
    assert rfb.y + rfb.height > mfp.y + mfp.height

    # RFB box must match landscape panel y and height
    assert rfb.y == layout.landscape_panel.y
    assert rfb.height == layout.landscape_panel.height

    # Side margins should be equal (status strip centered)
    left_margin = strip.x - rfb.x
    right_margin = (rfb.x + rfb.width) - (strip.x + strip.width)
    assert left_margin == right_margin


def test_primary_shadow_peeks_behind_primary_panel():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    shadow = layout.primary_shadow
    panel = layout.primary_panel

    # Shadow must be offset to the bottom-right
    assert shadow.x > panel.x
    assert shadow.y > panel.y
    # Shadow must be same size as primary panel
    assert shadow.width == panel.width
    assert shadow.height == panel.height
    # Bottom and right edges must peek past the primary panel
    assert shadow.x + shadow.width > panel.x + panel.width
    assert shadow.y + shadow.height > panel.y + panel.height


def test_mfp_height_matches_hw_ratio():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    mfp = layout.mfp_panel
    assert mfp.height == round(mfp.width * 1.125)


def test_genau_mode_toggle_does_not_overlap_clipper_save():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    genau = layout.genau_mode_toggle
    clipper = layout.clipper_save
    # Rects must not overlap — if they share any pixel,
    # mousePressEvent dispatches the wrong action.
    no_overlap = (
        genau.x + genau.width <= clipper.x
        or clipper.x + clipper.width <= genau.x
        or genau.y + genau.height <= clipper.y
        or clipper.y + clipper.height <= genau.y
    )
    assert no_overlap, (
        f"genau_mode_toggle {genau} overlaps clipper_save {clipper}"
    )


def _inside(inner, outer):
    """Return True if *inner* Rect fits entirely inside *outer* Rect."""
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def test_genau_param_buttons_inside_primary_panel():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )
    panel = layout.primary_panel

    for rect in (
        layout.genau_amp_up, layout.genau_amp_down,
        layout.genau_ctr_up, layout.genau_ctr_down,
        layout.genau_spd_up, layout.genau_spd_down,
    ):
        assert _inside(rect, panel), f"{rect} outside primary panel {panel}"


def test_genau_cruise_and_shape_inside_primary_panel():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )
    panel = layout.primary_panel

    assert _inside(layout.genau_cruise, panel)
    assert _inside(layout.genau_shape, panel)


def test_genau_cruise_is_left_of_shape():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.genau_cruise.x + layout.genau_cruise.width < layout.genau_shape.x


def test_genau_shape_does_not_overlap_mode_toggle():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    shape = layout.genau_shape
    toggle = layout.genau_mode_toggle
    no_overlap = (
        shape.x + shape.width <= toggle.x
        or toggle.x + toggle.width <= shape.x
        or shape.y + shape.height <= toggle.y
        or toggle.y + toggle.height <= shape.y
    )
    assert no_overlap, f"genau_shape {shape} overlaps genau_mode_toggle {toggle}"


def test_genau_shape_is_horizontally_centered():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    panel = layout.primary_panel
    panel_center = panel.x + panel.width // 2
    shape_center = layout.genau_shape.x + layout.genau_shape.width // 2
    assert abs(panel_center - shape_center) <= 1
