from __future__ import annotations

import math
from dataclasses import dataclass

from fun_time.config import LayoutConfig


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class Size:
    width: int
    height: int


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def client_rect_filling_frame(
    rect: Rect, *, left: int, top: int, right: int, bottom: int
) -> tuple[int, int, int, int]:
    """Client ``(x, y, w, h)`` so a decorated window's whole FRAME fills *rect*.

    ``QWidget.setGeometry`` positions the *client* area; the window manager draws
    the title bar and borders outside it, so setting the client to *rect* leaves
    the title bar overhanging *rect*'s top by its height.  Insetting the client
    by the frame margins drops it down and shrinks it to match, so the decorated
    window — chrome included — occupies exactly *rect*.  Zero margins (an
    undecorated window) leave *rect* unchanged.
    """
    return (
        rect.x + left,
        rect.y + top,
        max(0, rect.width - left - right),
        max(0, rect.height - top - bottom),
    )


@dataclass(frozen=True)
class DashboardPreviewLayout:
    dashboard_width: int
    dashboard_height: int
    main_monitor: Rect
    secondary_monitor: Rect
    dash_panel: Rect
    log_panel: Rect
    rfb_panel: Rect
    landscape_panel: Rect
    portrait_panel: Rect
    primary_panel: Rect
    primary_shadow: Rect
    portrait_prev: Rect
    portrait_next: Rect
    portrait_lock: Rect
    portrait_trash: Rect
    primary_prev: Rect
    primary_next: Rect
    quarter_button: Rect
    open_file_dialog: Rect
    clipper_save: Rect
    nau_record: Rect
    primary_nudge_prev: Rect
    primary_nudge_next: Rect
    landscape_prev: Rect
    landscape_next: Rect
    landscape_lock: Rect
    landscape_trash: Rect
    osr2_panel: Rect
    genau_mode_toggle: Rect
    genau_amp_label: Rect
    genau_amp_up: Rect
    genau_amp_down: Rect
    genau_ctr_label: Rect
    genau_ctr_up: Rect
    genau_ctr_down: Rect
    genau_spd_label: Rect
    genau_spd_up: Rect
    genau_spd_down: Rect
    genau_takeover: Rect
    genau_cruise: Rect
    hybrid_cruise: Rect
    genau_shape: Rect
    hybrid_mode_button: Rect
    nau_mode_button: Rect
    hybrid_quarter_button: Rect
    hybrid_open_file_dialog: Rect
    hybrid_genau_amp_label: Rect
    hybrid_genau_amp_up: Rect
    hybrid_genau_amp_down: Rect
    hybrid_genau_ctr_label: Rect
    hybrid_genau_ctr_up: Rect
    hybrid_genau_ctr_down: Rect
    hybrid_genau_spd_label: Rect
    hybrid_genau_spd_up: Rect
    hybrid_genau_spd_down: Rect
    quit_button: Rect
    omnipause_button: Rect
    help_button: Rect
    broker_panel: Rect
    fmode_panel: Rect
    voice_panel: Rect
    app_icon: Rect
    app_title: Rect


def compute_dashboard_preview_layout(
    main_monitor: Size,
    secondary_monitor: Size,
    layout_config: LayoutConfig,
    *,
    preview_max_h: float = 375,
) -> DashboardPreviewLayout:
    """The dashboard's schematic of the two monitors, at its natural scale.

    The main monitor's left column holds the dashboard itself beside the log
    panel, and the schematic draws both.  How wide the log box should be depends
    on how wide the dashboard window ends up — which in turn depends on the log
    box, because the box is what forces the schematic's left column wider.  Bisect
    for the width where the drawn dash:log split matches the real one.
    """
    real_left_w = main_monitor.width - int(main_monitor.width * clamp01(layout_config.landscape_width_ratio))

    def _at(log_box_w: int) -> DashboardPreviewLayout:
        return _preview_layout_with_log_box(
            main_monitor, secondary_monitor, layout_config, preview_max_h, log_box_w,
        )

    # The drawn log share rises with log_box_w while the real one falls (a wider
    # log box widens the dashboard, leaving the real log panel less room), so the
    # two cross exactly once.
    lo, hi = 0, max(1, real_left_w)
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = _at(mid)
        real_log_share = (real_left_w - candidate.dashboard_width) / real_left_w
        drawn = candidate.log_panel.width
        drawn_log_share = drawn / (candidate.dash_panel.width + drawn)
        if drawn_log_share < real_log_share:
            lo = mid + 1
        else:
            hi = mid
    return _at(lo)


def _preview_layout_with_log_box(
    main_monitor: Size,
    secondary_monitor: Size,
    layout_config: LayoutConfig,
    preview_max_h: float,
    log_box_w: int,
) -> DashboardPreviewLayout:
    outer_pad = 15
    bottom_pad = 9
    top_y = outer_pad
    monitor_gap = 15
    base_scale = preview_max_h / max(main_monitor.height, secondary_monitor.height)

    inner_pad = 15
    panel_gap = 12
    portrait_units = 7
    primary_units = 4
    stack_gap = 12

    # Mini button sizes used inside the dash box schematic
    mini_button_w = 16
    mini_button_h = 16
    mini_button_gap = 2

    # The dash box has two rows: (1) quit+omnipause+help buttons, (2) broker+
    # fmode+voice chips.  Its width is the floor for the schematic's left column.
    strip_pad = 3
    row_gap = 3
    dash_panel_h = strip_pad + mini_button_h + row_gap + mini_button_h + strip_pad
    mini_buttons_total_w = mini_button_w * 3 + mini_button_gap * 2
    dash_panel_w = mini_buttons_total_w + strip_pad * 2

    # The main monitor's left column must hold the dash box and the log box side
    # by side.  The mini buttons cannot shrink below legibility, so at this scale
    # an honest left column comes out a few pixels too narrow — stretch the main
    # monitor horizontally, but only by the shortfall.
    landscape_ratio = min(0.99, clamp01(layout_config.landscape_width_ratio))
    left_column_w = dash_panel_w + log_box_w
    needed_inner_w = math.ceil((left_column_w + panel_gap) / (1.0 - landscape_ratio))
    left_w = max(round(main_monitor.width * base_scale), needed_inner_w + inner_pad * 2)
    left_h = round(main_monitor.height * base_scale)
    right_w = round(secondary_monitor.width * base_scale)
    right_h = round(secondary_monitor.height * base_scale)
    main_x = outer_pad
    secondary_x = main_x + left_w + monitor_gap
    secondary_y = top_y

    right_inner_x = secondary_x + inner_pad
    right_inner_y = secondary_y + inner_pad
    right_inner_w = max(40, right_w - inner_pad * 2)
    right_inner_h = max(40, right_h - inner_pad * 2)
    available_stack_h = max(80, right_inner_h - stack_gap)
    unit_h = max(10, available_stack_h // (portrait_units + primary_units))
    portrait_h = max(52, unit_h * portrait_units)
    primary_h = max(48, available_stack_h - portrait_h)
    portrait_y = right_inner_y
    primary_y = right_inner_y + portrait_h + stack_gap
    shadow_offset = 4
    primary_shadow_x = right_inner_x + shadow_offset
    primary_shadow_y = primary_y + shadow_offset

    main_y = portrait_y + (portrait_h - left_h) // 2
    preview_bottom = max(main_y + left_h, secondary_y + right_h, primary_y + primary_h)

    main_inner_x = main_x + inner_pad
    main_inner_y = main_y + inner_pad
    main_inner_w = max(40, left_w - inner_pad * 2)
    main_inner_h = max(40, left_h - inner_pad * 2)

    landscape_w = max(34, int(main_inner_w * landscape_ratio))
    left_strip_w = main_inner_w - landscape_w - panel_gap

    # The left column, top to bottom: the dash box beside the log box, then the
    # RFB filling the rest — exactly how the three windows sit on the real
    # monitor.  Rounding slack in landscape_w lands in the log box.
    dash_x = main_inner_x
    dash_y = main_inner_y
    log_x = dash_x + dash_panel_w
    log_w = left_strip_w - dash_panel_w
    rfb_y = main_inner_y + dash_panel_h
    rfb_h = main_inner_h - dash_panel_h
    landscape_x = main_inner_x + left_strip_w + panel_gap
    landscape_y = main_inner_y

    # Button row (row 1) inside the dash box
    btn_row_x = dash_x + strip_pad
    btn_row_y = dash_y + strip_pad
    # Chip row (row 2) inside the dash box — same size as the button row
    chip_row_y = btn_row_y + mini_button_h + row_gap
    status_row_x = btn_row_x

    osr2_w = 66
    osr2_h = 66
    cable_gap = 8
    osr2_x = secondary_x - osr2_w - cable_gap * 2 - 62
    osr2_y = primary_y + (primary_h - osr2_h) // 2
    portrait_button_y = portrait_y + (portrait_h - 22) // 2
    portrait_stack_y = portrait_y + (portrait_h - 36) // 2
    primary_center_y = primary_y + (primary_h - 16) // 2 + 8
    primary_button_y = primary_center_y + 8 - 11  # center 22px-tall nav buttons on 16px-tall center row
    landscape_button_y = landscape_y + (main_inner_h - 22) // 2
    landscape_stack_y = landscape_y + (main_inner_h - 36) // 2

    # Genau parameter groups (AMP, CTR, SPD) — top row of primary panel
    # Each group: rotated label on left, ^/v buttons stacked on right.
    # Label is taller than the button pair so rotated text has room.
    genau_label_w = 12
    genau_label_h = 30
    genau_btn_w = 14
    genau_btn_h = 10
    genau_btn_pair_h = genau_btn_h * 2
    genau_btn_offset_y = (genau_label_h - genau_btn_pair_h) // 2
    genau_group_w = genau_label_w + genau_btn_w
    genau_group_gap = 10
    genau_groups_total_w = genau_group_w * 3 + genau_group_gap * 2
    genau_groups_x = right_inner_x + (right_inner_w - genau_groups_total_w) // 2
    # Center row between title bottom and nav button top
    genau_title_bottom = primary_y + 14
    genau_groups_y = genau_title_bottom + (primary_button_y - genau_title_bottom - genau_label_h) // 2

    def _genau_group_rects(col_index: int, group_y: int) -> tuple[Rect, Rect, Rect]:
        """Return (label, up_button, down_button) for a parameter group at *group_y*."""
        gx = genau_groups_x + col_index * (genau_group_w + genau_group_gap)
        btn_y = group_y + genau_btn_offset_y
        return (
            Rect(gx, group_y, genau_label_w, genau_label_h),
            Rect(gx + genau_label_w, btn_y, genau_btn_w, genau_btn_h),
            Rect(gx + genau_label_w, btn_y + genau_btn_h, genau_btn_w, genau_btn_h),
        )

    amp_label, amp_up, amp_down = _genau_group_rects(0, genau_groups_y)
    ctr_label, ctr_up, ctr_down = _genau_group_rects(1, genau_groups_y)
    spd_label, spd_up, spd_down = _genau_group_rects(2, genau_groups_y)

    # Hybrid genau groups — same horizontal positions, shifted up to clear nudge row
    hybrid_genau_groups_y = genau_title_bottom + 2
    h_amp_label, h_amp_up, h_amp_down = _genau_group_rects(0, hybrid_genau_groups_y)
    h_ctr_label, h_ctr_up, h_ctr_down = _genau_group_rects(1, hybrid_genau_groups_y)
    h_spd_label, h_spd_up, h_spd_down = _genau_group_rects(2, hybrid_genau_groups_y)

    # Hybrid mode button — to the left of genau_mode_toggle with 4px gap
    genau_toggle_x = right_inner_x + right_inner_w - 28
    genau_toggle_y = primary_y + primary_h - 20
    hybrid_mode_btn_w = 14
    hybrid_mode_btn_h = 16
    hybrid_mode_btn_x = genau_toggle_x - 4 - hybrid_mode_btn_w

    # Hybrid quarter/file dialog — at nudge button x positions, center y
    nudge_prev_x = right_inner_x + (right_inner_w - 44) // 2
    nudge_next_x = nudge_prev_x + 24

    # Bottom row of the primary panel. The bottom-left corner holds the cruise
    # (cc) button in Genau mode, but the Genau takeover toggle in VLC/Hybrid mode
    # (same coords, mutually exclusive by mode). In Hybrid, cruise shifts right to
    # hybrid_cruise so it can sit beside the takeover toggle.
    genau_bottom_y = primary_y + primary_h - 20
    genau_bottom_btn_w = 20
    genau_bottom_btn_h = 16
    genau_takeover_rect = Rect(
        right_inner_x + 4, genau_bottom_y,
        genau_bottom_btn_w, genau_bottom_btn_h,
    )
    genau_cruise_rect = Rect(
        right_inner_x + 4, genau_bottom_y,
        genau_bottom_btn_w, genau_bottom_btn_h,
    )
    hybrid_cruise_rect = Rect(
        right_inner_x + 4 + genau_bottom_btn_w + 4, genau_bottom_y,
        genau_bottom_btn_w, genau_bottom_btn_h,
    )
    genau_shape_rect = Rect(
        right_inner_x + (right_inner_w - genau_bottom_btn_w) // 2, genau_bottom_y,
        genau_bottom_btn_w, genau_bottom_btn_h,
    )

    # Nau-mode record button — one row below clipper_save (primary_center_y + 40).
    # If that would spill past the primary panel bottom, place it one row above
    # clipper_save instead so it stays inside the panel.
    nau_record_w = 28
    nau_record_h = 16
    nau_record_x = right_inner_x + (right_inner_w - nau_record_w) // 2
    nau_record_y = primary_center_y + 40
    if nau_record_y + nau_record_h > primary_y + primary_h:
        nau_record_y = primary_center_y - 40
    nau_record_rect = Rect(nau_record_x, nau_record_y, nau_record_w, nau_record_h)

    # App-name lockup — the icon followed by "Fun Time" — in the empty band
    # above the main-monitor mini-map, at the scene's top-left corner.  Sized a
    # touch larger than the individual box titles.  main_y is that band's floor.
    app_row_h = 30
    app_icon_size = 28
    app_row_y = max(1, (main_y - app_row_h) // 2)
    app_icon_rect = Rect(
        main_x, app_row_y + (app_row_h - app_icon_size) // 2, app_icon_size, app_icon_size
    )
    app_title_rect = Rect(main_x + app_icon_size + 6, app_row_y, 130, app_row_h)

    dashboard_w = secondary_x + right_w + outer_pad
    dashboard_h = max(preview_bottom, osr2_y + osr2_h, primary_shadow_y + primary_h) + bottom_pad

    return DashboardPreviewLayout(
        dashboard_width=dashboard_w,
        dashboard_height=dashboard_h,
        main_monitor=Rect(main_x, main_y, left_w, left_h),
        secondary_monitor=Rect(secondary_x, secondary_y, right_w, right_h),
        dash_panel=Rect(dash_x, dash_y, dash_panel_w, dash_panel_h),
        log_panel=Rect(log_x, dash_y, log_w, dash_panel_h),
        rfb_panel=Rect(main_inner_x, rfb_y, left_strip_w, rfb_h),
        landscape_panel=Rect(landscape_x, landscape_y, landscape_w, main_inner_h),
        portrait_panel=Rect(right_inner_x, portrait_y, right_inner_w, portrait_h),
        primary_panel=Rect(right_inner_x, primary_y, right_inner_w, primary_h),
        primary_shadow=Rect(primary_shadow_x, primary_shadow_y, right_inner_w, primary_h),
        portrait_prev=Rect(right_inner_x + 6, portrait_button_y, 18, 22),
        portrait_next=Rect(right_inner_x + right_inner_w - 24, portrait_button_y, 18, 22),
        portrait_trash=Rect(right_inner_x + (right_inner_w - 30) // 2, portrait_stack_y, 30, 16),
        portrait_lock=Rect(right_inner_x + (right_inner_w - 30) // 2, portrait_stack_y + 20, 30, 16),
        primary_prev=Rect(right_inner_x + 6, primary_button_y, 18, 22),
        primary_next=Rect(right_inner_x + right_inner_w - 24, primary_button_y, 18, 22),
        quarter_button=Rect(right_inner_x + (right_inner_w - 28) // 2, primary_center_y, 28, 16),
        open_file_dialog=Rect(right_inner_x + (right_inner_w - 28) // 2, primary_center_y, 28, 16),
        clipper_save=Rect(right_inner_x + (right_inner_w - 28) // 2, primary_center_y + 20, 28, 16),
        nau_record=nau_record_rect,
        primary_nudge_prev=Rect(right_inner_x + (right_inner_w - 44) // 2, primary_center_y - 20, 20, 16),
        primary_nudge_next=Rect(right_inner_x + (right_inner_w - 44) // 2 + 24, primary_center_y - 20, 20, 16),
        landscape_prev=Rect(landscape_x + 6, landscape_button_y, 18, 22),
        landscape_next=Rect(landscape_x + landscape_w - 24, landscape_button_y, 18, 22),
        landscape_trash=Rect(landscape_x + (landscape_w - 30) // 2, landscape_stack_y, 30, 16),
        landscape_lock=Rect(landscape_x + (landscape_w - 30) // 2, landscape_stack_y + 20, 30, 16),
        osr2_panel=Rect(osr2_x, osr2_y, osr2_w, osr2_h),
        genau_mode_toggle=Rect(right_inner_x + right_inner_w - 28, primary_y + primary_h - 20, 28, 16),
        genau_amp_label=amp_label,
        genau_amp_up=amp_up,
        genau_amp_down=amp_down,
        genau_ctr_label=ctr_label,
        genau_ctr_up=ctr_up,
        genau_ctr_down=ctr_down,
        genau_spd_label=spd_label,
        genau_spd_up=spd_up,
        genau_spd_down=spd_down,
        genau_takeover=genau_takeover_rect,
        genau_cruise=genau_cruise_rect,
        hybrid_cruise=hybrid_cruise_rect,
        genau_shape=genau_shape_rect,
        hybrid_mode_button=Rect(hybrid_mode_btn_x, genau_toggle_y, hybrid_mode_btn_w, hybrid_mode_btn_h),
        nau_mode_button=Rect(genau_toggle_x, genau_toggle_y, 28, 16),
        hybrid_quarter_button=Rect(nudge_prev_x, primary_center_y, genau_bottom_btn_w, genau_bottom_btn_h),
        hybrid_open_file_dialog=Rect(nudge_next_x, primary_center_y, genau_bottom_btn_w, genau_bottom_btn_h),
        hybrid_genau_amp_label=h_amp_label,
        hybrid_genau_amp_up=h_amp_up,
        hybrid_genau_amp_down=h_amp_down,
        hybrid_genau_ctr_label=h_ctr_label,
        hybrid_genau_ctr_up=h_ctr_up,
        hybrid_genau_ctr_down=h_ctr_down,
        hybrid_genau_spd_label=h_spd_label,
        hybrid_genau_spd_up=h_spd_up,
        hybrid_genau_spd_down=h_spd_down,
        quit_button=Rect(btn_row_x, btn_row_y, mini_button_w, mini_button_h),
        omnipause_button=Rect(btn_row_x + mini_button_w + mini_button_gap, btn_row_y, mini_button_w, mini_button_h),
        help_button=Rect(btn_row_x + (mini_button_w + mini_button_gap) * 2, btn_row_y, mini_button_w, mini_button_h),
        broker_panel=Rect(status_row_x, chip_row_y, mini_button_w, mini_button_h),
        fmode_panel=Rect(status_row_x + mini_button_w + mini_button_gap, chip_row_y, mini_button_w, mini_button_h),
        voice_panel=Rect(status_row_x + (mini_button_w + mini_button_gap) * 2, chip_row_y, mini_button_w, mini_button_h),
        app_icon=app_icon_rect,
        app_title=app_title_rect,
    )
