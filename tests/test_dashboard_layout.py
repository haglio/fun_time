from __future__ import annotations

from fun_time.config import LayoutConfig
from fun_time.dashboard_layout import (
    Rect,
    Size,
    client_rect_filling_frame,
    compute_dashboard_preview_layout,
)


def _layout_config() -> LayoutConfig:
    return LayoutConfig(
        main_monitor=1,
        secondary_monitor=2,
        primary_top_ratio=0.7272727273,
        landscape_width_ratio=0.6666666667,
    )


def test_dashboard_preview_layout_uses_monitor_proportions_directly():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.main_monitor.height == 152
    assert layout.secondary_monitor.width == 157
    assert layout.secondary_monitor.height == 375
    # The main monitor is stretched horizontally past its true 279px so the left
    # column can hold the dash box beside the log box — but only by that much.
    assert 279 <= layout.main_monitor.width <= 279 * 1.25


def test_dash_and_log_boxes_split_the_left_column_as_the_windows_do():
    """The schematic is a map of the real screen, so the drawn dash:log split
    must match the split the two real windows make of the main monitor's left
    column."""
    cfg = _layout_config()
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392), Size(width=1440, height=3440), cfg,
    )

    real_left_w = 2560 - int(2560 * cfg.landscape_width_ratio)
    real_log_share = (real_left_w - layout.dashboard_width) / real_left_w

    drawn = layout.log_panel.width
    drawn_log_share = drawn / (layout.dash_panel.width + drawn)

    assert abs(drawn_log_share - real_log_share) < 0.02


def test_log_box_sits_beside_the_dash_box_above_the_rfb():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    dash, log, rfb = layout.dash_panel, layout.log_panel, layout.rfb_panel
    # Dash and log share a top edge and abut, as the two windows do on screen.
    assert log.x == dash.x + dash.width
    assert log.y == dash.y
    assert log.height == dash.height
    assert log.width > 0
    # The RFB starts at their shared bottom edge and spans the whole column.
    assert rfb.x == dash.x
    assert rfb.y == dash.y + dash.height
    assert rfb.width == dash.width + log.width
    # Together they fill the main monitor's left column, clear of the landscape.
    assert rfb.x + rfb.width <= layout.landscape_panel.x


def test_app_name_lockup_sits_in_the_top_left_band_above_the_schematic():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    icon, title = layout.app_icon, layout.app_title
    # The lockup lives in the empty band above the main-monitor mini-map ...
    assert icon.y + icon.height <= layout.main_monitor.y
    assert title.y + title.height <= layout.main_monitor.y
    # ... hence above the dash's own box, in the scene's top-left corner.
    assert title.y + title.height <= layout.dash_panel.y
    assert icon.x == layout.main_monitor.x
    # Icon first, then the name to its right.
    assert icon.x + icon.width <= title.x
    assert icon.width > 0 and title.width > 0


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


def test_help_button_in_dash_box_top_row_third_slot():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    strip = layout.dash_panel
    help_b = layout.help_button
    quit_b = layout.quit_button
    omni_b = layout.omnipause_button

    # Same size as the other mini buttons.
    assert help_b.width == quit_b.width
    assert help_b.height == quit_b.height
    # Sits in the top button row (same y as quit/omnipause).
    assert help_b.y == quit_b.y
    # Third slot: to the right of omnipause, aligned with the voice chip below.
    assert help_b.x > omni_b.x
    assert help_b.x == layout.voice_panel.x
    # Fully inside the status strip.
    assert help_b.x >= strip.x
    assert help_b.x + help_b.width <= strip.x + strip.width
    assert help_b.y + help_b.height <= strip.y + strip.height
    # No overlap with omnipause.
    assert omni_b.x + omni_b.width <= help_b.x


def test_client_rect_filling_frame_insets_by_the_chrome():
    """When a window's whole FRAME must fill the rect, its client drops below
    the title bar and shrinks, so the decorated window — not its content —
    occupies the rect."""
    rect = Rect(x=100, y=200, width=600, height=400)

    assert client_rect_filling_frame(rect, left=8, top=31, right=8, bottom=8) == (108, 231, 584, 361)


def test_client_rect_filling_frame_is_identity_without_decorations():
    """An undecorated (zero-margin) window's client already is its frame."""
    rect = Rect(x=0, y=0, width=300, height=300)

    assert client_rect_filling_frame(rect, left=0, top=0, right=0, bottom=0) == (0, 0, 300, 300)


def test_dash_box_side_margin_matches_top_margin():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    strip = layout.dash_panel
    btn = layout.quit_button
    side_margin = btn.x - strip.x
    top_margin = btn.y - strip.y
    assert side_margin == top_margin


def test_dash_box_holds_every_mini_button():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    for rect in (
        layout.quit_button, layout.omnipause_button, layout.help_button,
        layout.broker_panel, layout.fmode_panel, layout.voice_panel,
    ):
        assert _inside(rect, layout.dash_panel), f"{rect} outside dash box {layout.dash_panel}"


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
        layout.genau_amp_label, layout.genau_amp_up, layout.genau_amp_down,
        layout.genau_ctr_label, layout.genau_ctr_up, layout.genau_ctr_down,
        layout.genau_spd_label, layout.genau_spd_up, layout.genau_spd_down,
    ):
        assert _inside(rect, panel), f"{rect} outside primary panel {panel}"


def test_genau_param_labels_are_left_of_buttons():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    for label, up in (
        (layout.genau_amp_label, layout.genau_amp_up),
        (layout.genau_ctr_label, layout.genau_ctr_up),
        (layout.genau_spd_label, layout.genau_spd_up),
    ):
        assert label.x + label.width == up.x, (
            f"Label right edge ({label.x + label.width}) should abut button left edge ({up.x})"
        )


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


def test_genau_takeover_bottom_left_and_hybrid_cruise_to_its_right():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    takeover = layout.genau_takeover
    cruise = layout.genau_cruise
    hybrid_cruise = layout.hybrid_cruise
    assert _inside(takeover, layout.primary_panel)
    assert _inside(hybrid_cruise, layout.primary_panel)
    # In Genau mode the bottom-left holds cruise; in Nau/Hybrid the takeover toggle
    # takes that exact spot, so the two share coordinates.
    assert takeover.x == cruise.x and takeover.y == cruise.y
    # In Hybrid, cruise shifts right of the takeover toggle, no overlap.
    assert takeover.x < hybrid_cruise.x
    assert takeover.x + takeover.width <= hybrid_cruise.x


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


def test_hybrid_mode_button_left_of_genau_toggle():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    h = layout.hybrid_mode_button
    g = layout.genau_mode_toggle
    # hybrid_mode_button is to the left with a 4px gap
    assert h.x + h.width + 4 == g.x
    assert h.y == g.y
    assert h.width == 14
    assert h.height == 16
    assert _inside(h, layout.primary_panel)


def test_nau_mode_button_same_position_as_genau_toggle():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    assert layout.nau_mode_button.x == layout.genau_mode_toggle.x
    assert layout.nau_mode_button.y == layout.genau_mode_toggle.y
    assert layout.nau_mode_button.width == layout.genau_mode_toggle.width
    assert layout.nau_mode_button.height == layout.genau_mode_toggle.height


def test_hybrid_quarter_and_open_file_positions():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    # hybrid_quarter_button at nudge_prev x, center_y
    assert layout.hybrid_quarter_button.x == layout.primary_nudge_prev.x
    assert layout.hybrid_quarter_button.y == layout.quarter_button.y
    assert layout.hybrid_quarter_button.width == 20
    assert layout.hybrid_quarter_button.height == 16
    # hybrid_open_file_dialog at nudge_next x, center_y
    assert layout.hybrid_open_file_dialog.x == layout.primary_nudge_next.x
    assert layout.hybrid_open_file_dialog.y == layout.quarter_button.y
    assert layout.hybrid_open_file_dialog.width == 20
    assert layout.hybrid_open_file_dialog.height == 16
    assert _inside(layout.hybrid_quarter_button, layout.primary_panel)
    assert _inside(layout.hybrid_open_file_dialog, layout.primary_panel)


def test_hybrid_genau_groups_above_normal_groups():
    layout = compute_dashboard_preview_layout(
        Size(width=2560, height=1392),
        Size(width=1440, height=3440),
        _layout_config(),
    )

    # Hybrid groups should be shifted up compared to normal groups
    assert layout.hybrid_genau_amp_label.y < layout.genau_amp_label.y
    assert layout.hybrid_genau_ctr_label.y < layout.genau_ctr_label.y
    assert layout.hybrid_genau_spd_label.y < layout.genau_spd_label.y
    # All hybrid genau rects must fit inside primary panel
    for rect in (
        layout.hybrid_genau_amp_label, layout.hybrid_genau_amp_up, layout.hybrid_genau_amp_down,
        layout.hybrid_genau_ctr_label, layout.hybrid_genau_ctr_up, layout.hybrid_genau_ctr_down,
        layout.hybrid_genau_spd_label, layout.hybrid_genau_spd_up, layout.hybrid_genau_spd_down,
    ):
        assert _inside(rect, layout.primary_panel), f"{rect} outside primary panel {layout.primary_panel}"
