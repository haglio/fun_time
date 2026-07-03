from __future__ import annotations

from dataclasses import dataclass

from fun_time.config import LayoutConfig
from fun_time.dashboard_layout import Size, compute_dashboard_preview_layout


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class MonitorRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class WindowRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class WindowLayoutPlan:
    portrait: WindowRect
    primary: WindowRect
    landscape: WindowRect
    dashboard: WindowRect
    random_favs_browser: WindowRect


def compute_window_layout(
    *,
    main_monitor: MonitorRect,
    secondary_monitor: MonitorRect,
    layout_config: LayoutConfig,
    dashboard_chrome_height: int = 0,
) -> WindowLayoutPlan:
    dashboard_size = compute_dashboard_size(
        main_monitor=main_monitor,
        secondary_monitor=secondary_monitor,
        layout_config=layout_config,
    )
    portrait_height = int(secondary_monitor.height * clamp01(layout_config.primary_top_ratio))
    primary_height = secondary_monitor.height - portrait_height

    portrait = WindowRect(
        x=secondary_monitor.x,
        y=secondary_monitor.y,
        width=secondary_monitor.width,
        height=portrait_height,
    )
    # The three primary-display players (VLC, Genau, Nau) share one slot.
    primary = WindowRect(
        x=secondary_monitor.x,
        y=secondary_monitor.y + portrait_height,
        width=secondary_monitor.width,
        height=primary_height,
    )

    landscape_width = int(main_monitor.width * clamp01(layout_config.landscape_width_ratio))
    landscape = WindowRect(
        x=main_monitor.x + (main_monitor.width - landscape_width),
        y=main_monitor.y,
        width=landscape_width,
        height=main_monitor.height,
    )

    random_favs_browser = WindowRect(
        x=main_monitor.x,
        y=main_monitor.y,
        width=main_monitor.width - landscape_width,
        height=main_monitor.height,
    )

    dashboard = compute_left_partition_dashboard(
        main_monitor=main_monitor,
        layout_config=layout_config,
        dashboard_size=dashboard_size,
        dashboard_chrome_height=dashboard_chrome_height,
    )

    return WindowLayoutPlan(
        portrait=portrait,
        primary=primary,
        landscape=landscape,
        dashboard=dashboard,
        random_favs_browser=random_favs_browser,
    )


def compute_dashboard_size(
    *,
    main_monitor: MonitorRect,
    secondary_monitor: MonitorRect,
    layout_config: LayoutConfig,
) -> Size:
    preview = compute_dashboard_preview_layout(
        Size(main_monitor.width, main_monitor.height),
        Size(secondary_monitor.width, secondary_monitor.height),
        layout_config,
    )
    return Size(preview.dashboard_width, preview.dashboard_height)


def compute_left_partition_dashboard(
    *,
    main_monitor: MonitorRect,
    layout_config: LayoutConfig,
    dashboard_size: Size,
    dashboard_chrome_height: int = 0,
) -> WindowRect:
    """Center the dashboard in the left partition (beside the landscape VLC)."""
    landscape_width = int(main_monitor.width * clamp01(layout_config.landscape_width_ratio))
    left_width = main_monitor.width - landscape_width
    top_margin = int(main_monitor.height * clamp01(layout_config.left_partition_top_ratio))
    bottom_margin = int(main_monitor.height * clamp01(layout_config.left_partition_bottom_ratio))
    dashboard_outer_h = dashboard_size.height + dashboard_chrome_height
    usable_height = main_monitor.height - top_margin - bottom_margin
    gap_y = (usable_height - dashboard_outer_h) // 2
    return WindowRect(
        x=main_monitor.x + (left_width - dashboard_size.width) // 2,
        y=main_monitor.y + top_margin + gap_y,
        width=dashboard_size.width,
        height=dashboard_size.height,
    )
