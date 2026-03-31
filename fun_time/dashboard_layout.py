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
    title: Rect
    main_status_strip: Rect
    mfp_panel: Rect
    landscape_panel: Rect
    portrait_panel: Rect
    primary_panel: Rect
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
    link_toggle: Rect
    quit_button: Rect
    omnipause_button: Rect
    broker_panel: Rect
    fmode_panel: Rect


def compute_dashboard_preview_layout(
    main_monitor: Size,
    secondary_monitor: Size,
    layout_config: LayoutConfig,
) -> DashboardPreviewLayout:
    outer_pad = 10
    bottom_pad = 6
    top_y = outer_pad
    monitor_gap = 10
    preview_max_h = 250
    base_scale = preview_max_h / max(main_monitor.height, secondary_monitor.height)

    left_w = round(main_monitor.width * base_scale)
    left_h = round(main_monitor.height * base_scale)
    right_w = round(secondary_monitor.width * base_scale)
    right_h = round(secondary_monitor.height * base_scale)
    main_x = outer_pad
    secondary_x = main_x + left_w + monitor_gap
    secondary_y = top_y

    inner_pad = 10
    panel_gap = 8
    status_chip_size = 12
    status_chip_gap = 1
    portrait_units = 7
    primary_units = 4
    stack_gap = 8

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

    # Mini button sizes used inside the status strip schematic
    mini_button_w = 20
    mini_button_h = 16
    mini_button_gap = 4

    main_y = portrait_y + (portrait_h - left_h) // 2
    preview_bottom = max(main_y + left_h, secondary_y + right_h, primary_y + primary_h)

    main_inner_x = main_x + inner_pad
    main_inner_y = main_y + inner_pad
    main_inner_w = max(40, left_w - inner_pad * 2)
    main_inner_h = max(40, left_h - inner_pad * 2)

    landscape_w = max(34, int(main_inner_w * clamp01(layout_config.landscape_width_ratio)))
    left_strip_w = max(52, main_inner_w - landscape_w - panel_gap)
    mfp_max_w = max(44, int(left_strip_w * clamp01(layout_config.mfp_width_ratio)))

    # Status strip has two rows: (1) quit+omnipause buttons, (2) broker+fmode chips
    strip_pad = 3
    row_gap = 3
    status_strip_h = strip_pad + mini_button_h + row_gap + status_chip_size + strip_pad
    mini_buttons_total_w = mini_button_w * 2 + mini_button_gap
    status_strip_w = max(mfp_max_w, status_chip_size * 2 + status_chip_gap + 8, mini_buttons_total_w)
    left_column_nudge = 2
    status_strip_x = main_inner_x + (left_strip_w - status_strip_w) // 2 - left_column_nudge
    status_strip_y = main_inner_y

    mfp_area_y = status_strip_y + status_strip_h + panel_gap
    mfp_area_h = max(28, main_inner_h - status_strip_h - panel_gap)
    mfp_preview_aspect = 0.67
    mfp_h = max(28, int(mfp_area_h * 0.92))
    mfp_w = min(mfp_max_w, round(mfp_h * mfp_preview_aspect))
    mfp_x = main_inner_x + (left_strip_w - mfp_w) // 2 - left_column_nudge
    mfp_y = mfp_area_y + (mfp_area_h - mfp_h) // 2
    landscape_x = main_inner_x + left_strip_w + panel_gap
    landscape_y = main_inner_y

    # Button row (row 1) inside status strip
    btn_row_x = status_strip_x + (status_strip_w - mini_buttons_total_w) // 2
    btn_row_y = status_strip_y + strip_pad
    # Chip row (row 2) inside status strip
    chip_row_y = btn_row_y + mini_button_h + row_gap
    status_row_x = status_strip_x + (status_strip_w - (status_chip_size * 2 + status_chip_gap)) // 2

    osr2_w = 56
    osr2_h = 56
    link_w = 62
    link_gap = 8
    osr2_x = secondary_x - osr2_w - link_gap - link_w - link_gap
    osr2_y = primary_y + (primary_h - osr2_h) // 2
    link_y = primary_y + (primary_h - 18) // 2
    link_x = osr2_x + osr2_w + link_gap
    portrait_button_y = portrait_y + (portrait_h - 22) // 2
    portrait_stack_y = portrait_y + (portrait_h - 36) // 2
    primary_button_y = primary_y + (primary_h - 22) // 2
    landscape_button_y = landscape_y + (main_inner_h - 22) // 2
    landscape_stack_y = landscape_y + (main_inner_h - 36) // 2

    dashboard_w = secondary_x + right_w + outer_pad
    dashboard_h = max(preview_bottom, osr2_y + osr2_h, link_y + 18) + bottom_pad

    return DashboardPreviewLayout(
        dashboard_width=dashboard_w,
        dashboard_height=dashboard_h,
        main_monitor=Rect(main_x, main_y, left_w, left_h),
        secondary_monitor=Rect(secondary_x, secondary_y, right_w, right_h),
        title=Rect(outer_pad, preview_bottom - 14, 88, 12),
        main_status_strip=Rect(status_strip_x, status_strip_y, status_strip_w, status_strip_h),
        mfp_panel=Rect(mfp_x, mfp_y, mfp_w, mfp_h),
        landscape_panel=Rect(landscape_x, landscape_y, landscape_w, main_inner_h),
        portrait_panel=Rect(right_inner_x, portrait_y, right_inner_w, portrait_h),
        primary_panel=Rect(right_inner_x, primary_y, right_inner_w, primary_h),
        portrait_prev=Rect(right_inner_x + 6, portrait_button_y, 18, 22),
        portrait_next=Rect(right_inner_x + right_inner_w - 24, portrait_button_y, 18, 22),
        portrait_trash=Rect(right_inner_x + (right_inner_w - 30) // 2, portrait_stack_y, 30, 16),
        portrait_lock=Rect(right_inner_x + (right_inner_w - 30) // 2, portrait_stack_y + 20, 30, 16),
        primary_prev=Rect(right_inner_x + 6, primary_button_y, 18, 22),
        primary_next=Rect(right_inner_x + right_inner_w - 24, primary_button_y, 18, 22),
        quarter_button=Rect(right_inner_x + (right_inner_w - 28) // 2, primary_y + (primary_h - 16) // 2, 28, 16),
        open_file_dialog=Rect(right_inner_x + (right_inner_w - 28) // 2, primary_y + (primary_h - 16) // 2, 28, 16),
        clipper_save=Rect(right_inner_x + (right_inner_w - 28) // 2, primary_y + (primary_h - 16) // 2 + 20, 28, 16),
        vlc_nudge_prev=Rect(right_inner_x + (right_inner_w - 44) // 2, primary_y + (primary_h - 16) // 2 - 20, 20, 16),
        vlc_nudge_next=Rect(right_inner_x + (right_inner_w - 44) // 2 + 24, primary_y + (primary_h - 16) // 2 - 20, 20, 16),
        landscape_prev=Rect(landscape_x + 6, landscape_button_y, 18, 22),
        landscape_next=Rect(landscape_x + landscape_w - 24, landscape_button_y, 18, 22),
        landscape_trash=Rect(landscape_x + (landscape_w - 30) // 2, landscape_stack_y, 30, 16),
        landscape_lock=Rect(landscape_x + (landscape_w - 30) // 2, landscape_stack_y + 20, 30, 16),
        osr2_panel=Rect(osr2_x, osr2_y, osr2_w, osr2_h),
        link_toggle=Rect(link_x, link_y, link_w, 18),
        quit_button=Rect(btn_row_x, btn_row_y, mini_button_w, mini_button_h),
        omnipause_button=Rect(btn_row_x + mini_button_w + mini_button_gap, btn_row_y, mini_button_w, mini_button_h),
        broker_panel=Rect(status_row_x, chip_row_y, status_chip_size, status_chip_size),
        fmode_panel=Rect(status_row_x + status_chip_size + status_chip_gap, chip_row_y, status_chip_size, status_chip_size),
    )
