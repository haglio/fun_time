from __future__ import annotations

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


@dataclass(frozen=True)
class DashboardPreviewLayout:
    dashboard_width: int
    dashboard_height: int
    main_monitor: Rect
    secondary_monitor: Rect
    main_status_strip: Rect
    rfb_panel: Rect
    mfp_panel: Rect
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
    vlc_nudge_prev: Rect
    vlc_nudge_next: Rect
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
    genau_cruise: Rect
    genau_shape: Rect
    quit_button: Rect
    omnipause_button: Rect
    broker_panel: Rect
    fmode_panel: Rect
    voice_panel: Rect


def compute_dashboard_preview_layout(
    main_monitor: Size,
    secondary_monitor: Size,
    layout_config: LayoutConfig,
) -> DashboardPreviewLayout:
    outer_pad = 15
    bottom_pad = 9
    top_y = outer_pad
    monitor_gap = 15
    preview_max_h = 375
    base_scale = preview_max_h / max(main_monitor.height, secondary_monitor.height)

    left_w = round(main_monitor.width * base_scale)
    left_h = round(main_monitor.height * base_scale)
    right_w = round(secondary_monitor.width * base_scale)
    right_h = round(secondary_monitor.height * base_scale)
    main_x = outer_pad
    secondary_x = main_x + left_w + monitor_gap
    secondary_y = top_y

    inner_pad = 15
    panel_gap = 12
    portrait_units = 7
    primary_units = 4
    stack_gap = 12

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

    # Mini button sizes used inside the status strip schematic
    mini_button_w = 20
    mini_button_h = 16
    mini_button_gap = 3

    main_y = portrait_y + (portrait_h - left_h) // 2
    preview_bottom = max(main_y + left_h, secondary_y + right_h, primary_y + primary_h)

    main_inner_x = main_x + inner_pad
    main_inner_y = main_y + inner_pad
    main_inner_w = max(40, left_w - inner_pad * 2)
    main_inner_h = max(40, left_h - inner_pad * 2)

    landscape_w = max(34, int(main_inner_w * clamp01(layout_config.landscape_width_ratio)))
    left_strip_w = max(52, main_inner_w - landscape_w - panel_gap)
    mfp_max_w = max(44, int(left_strip_w * clamp01(layout_config.mfp_width_ratio)))

    # Status strip has two rows: (1) quit+omnipause buttons, (2) broker+fmode+voice chips
    strip_pad = 3
    row_gap = 3
    status_strip_h = strip_pad + mini_button_h + row_gap + mini_button_h + strip_pad
    mini_buttons_total_w = mini_button_w * 3 + mini_button_gap * 2
    status_strip_w = max(mfp_max_w, mini_buttons_total_w + strip_pad * 2)
    # RFB container around status strip + MFP
    rfb_pad = 3
    rfb_gap = 4
    rfb_y = main_inner_y
    rfb_h = main_inner_h
    rfb_w = status_strip_w + 2 * rfb_pad
    rfb_x = main_inner_x + (left_strip_w - rfb_w) // 2

    status_strip_x = rfb_x + (rfb_w - status_strip_w) // 2
    status_strip_y = rfb_y + rfb_pad

    mfp_area_y = status_strip_y + status_strip_h + rfb_gap
    mfp_area_h = max(28, rfb_h - 2 * rfb_pad - status_strip_h - rfb_gap)
    mfp_hw_ratio = 1.125
    mfp_h_raw = max(28, int(mfp_area_h * 0.92))
    mfp_w = min(mfp_max_w, round(mfp_h_raw / mfp_hw_ratio))
    mfp_h = round(mfp_w * mfp_hw_ratio)
    mfp_x = rfb_x + (rfb_w - mfp_w) // 2
    mfp_y = mfp_area_y + (mfp_area_h - mfp_h) // 2
    landscape_x = main_inner_x + left_strip_w + panel_gap
    landscape_y = main_inner_y

    # Button row (row 1) inside status strip
    btn_row_x = status_strip_x + (status_strip_w - mini_buttons_total_w) // 2
    btn_row_y = status_strip_y + strip_pad
    # Chip row (row 2) inside status strip — same size as button row
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
    # Each group: rotated label on left, ^/v buttons stacked on right
    genau_label_w = 12
    genau_btn_w = 14
    genau_btn_h = 10
    genau_group_w = genau_label_w + genau_btn_w  # 26
    genau_group_h = genau_btn_h * 2  # 20
    genau_group_gap = 6
    genau_groups_total_w = genau_group_w * 3 + genau_group_gap * 2
    genau_groups_x = right_inner_x + (right_inner_w - genau_groups_total_w) // 2
    genau_groups_y = primary_y + 16  # just below label

    def _genau_group_rects(col_index: int) -> tuple[Rect, Rect, Rect]:
        """Return (label, up_button, down_button) for a parameter group."""
        gx = genau_groups_x + col_index * (genau_group_w + genau_group_gap)
        return (
            Rect(gx, genau_groups_y, genau_label_w, genau_group_h),
            Rect(gx + genau_label_w, genau_groups_y, genau_btn_w, genau_btn_h),
            Rect(gx + genau_label_w, genau_groups_y + genau_btn_h, genau_btn_w, genau_btn_h),
        )

    amp_label, amp_up, amp_down = _genau_group_rects(0)
    ctr_label, ctr_up, ctr_down = _genau_group_rects(1)
    spd_label, spd_up, spd_down = _genau_group_rects(2)

    # Genau cruise / shape — bottom row alongside mode toggle
    genau_bottom_y = primary_y + primary_h - 20
    genau_bottom_btn_w = 20
    genau_bottom_btn_h = 16
    genau_cruise_rect = Rect(
        right_inner_x + 4, genau_bottom_y,
        genau_bottom_btn_w, genau_bottom_btn_h,
    )
    genau_shape_rect = Rect(
        right_inner_x + (right_inner_w - genau_bottom_btn_w) // 2, genau_bottom_y,
        genau_bottom_btn_w, genau_bottom_btn_h,
    )

    dashboard_w = secondary_x + right_w + outer_pad
    dashboard_h = max(preview_bottom, osr2_y + osr2_h, primary_shadow_y + primary_h) + bottom_pad

    return DashboardPreviewLayout(
        dashboard_width=dashboard_w,
        dashboard_height=dashboard_h,
        main_monitor=Rect(main_x, main_y, left_w, left_h),
        secondary_monitor=Rect(secondary_x, secondary_y, right_w, right_h),
        main_status_strip=Rect(status_strip_x, status_strip_y, status_strip_w, status_strip_h),
        rfb_panel=Rect(rfb_x, rfb_y, rfb_w, rfb_h),
        mfp_panel=Rect(mfp_x, mfp_y, mfp_w, mfp_h),
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
        vlc_nudge_prev=Rect(right_inner_x + (right_inner_w - 44) // 2, primary_center_y - 20, 20, 16),
        vlc_nudge_next=Rect(right_inner_x + (right_inner_w - 44) // 2 + 24, primary_center_y - 20, 20, 16),
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
        genau_cruise=genau_cruise_rect,
        genau_shape=genau_shape_rect,
        quit_button=Rect(btn_row_x, btn_row_y, mini_button_w, mini_button_h),
        omnipause_button=Rect(btn_row_x + mini_button_w + mini_button_gap, btn_row_y, mini_button_w, mini_button_h),
        broker_panel=Rect(status_row_x, chip_row_y, mini_button_w, mini_button_h),
        fmode_panel=Rect(status_row_x + mini_button_w + mini_button_gap, chip_row_y, mini_button_w, mini_button_h),
        voice_panel=Rect(status_row_x + (mini_button_w + mini_button_gap) * 2, chip_row_y, mini_button_w, mini_button_h),
    )
