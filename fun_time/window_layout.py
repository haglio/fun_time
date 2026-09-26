from __future__ import annotations

from dataclasses import dataclass

from fun_time.config import LayoutConfig
from fun_time.crown import Crown
from fun_time.dashboard_layout import Rect, dashboard_window_height
from fun_time.monitors import enumerate_monitors, get_logical_monitor_rects


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# One rectangle under the two names this module calls it by.
MonitorRect = Rect
WindowRect = Rect


@dataclass(frozen=True)
class WindowLayoutPlan:
    portrait: WindowRect
    landscape: WindowRect
    dashboard: WindowRect
    random_favs_browser: WindowRect


@dataclass(frozen=True)
class ScreenLayout:
    plan: WindowLayoutPlan
    config: LayoutConfig
    secondary_monitor: MonitorRect


def screen_layout(layout_config: LayoutConfig) -> ScreenLayout:
    """The plan for the monitors this machine has right now."""
    primary_rect, secondary_rect = get_logical_monitor_rects(
        enumerate_monitors(),
        primary_index=layout_config.primary_monitor,
        secondary_index=layout_config.secondary_monitor,
    )
    return ScreenLayout(
        plan=compute_window_layout(
            primary_monitor=primary_rect,
            secondary_monitor=secondary_rect,
            layout_config=layout_config,
        ),
        config=layout_config,
        secondary_monitor=secondary_rect,
    )


def compute_window_layout(
    *,
    primary_monitor: MonitorRect,
    secondary_monitor: MonitorRect,
    layout_config: LayoutConfig,
) -> WindowLayoutPlan:
    dashboard_height = dashboard_window_height()

    landscape_width = int(primary_monitor.width * clamp01(layout_config.landscape_width_ratio))
    landscape = WindowRect(
        x=primary_monitor.x + (primary_monitor.width - landscape_width),
        y=primary_monitor.y,
        width=landscape_width,
        height=primary_monitor.height,
    )

    # The left column stacks the dashboard above the RFB.  The dashboard spans
    # the full column width — its control bar across the top and the embedded log
    # stream filling everything under it — at its natural height.  The RFB then
    # fills the whole rectangle from the dashboard's lower edge down to the
    # monitor's lower edge.
    left_width = primary_monitor.width - landscape_width
    dashboard = WindowRect(
        x=primary_monitor.x,
        y=primary_monitor.y,
        width=left_width,
        height=dashboard_height,
    )
    random_favs_browser = WindowRect(
        x=primary_monitor.x,
        y=primary_monitor.y + dashboard_height,
        width=left_width,
        height=primary_monitor.height - dashboard_height,
    )

    return WindowLayoutPlan(
        portrait=secondary_monitor_rects(
            secondary_monitor, layout_config, majority=Crown.PORTRAIT).portrait,
        landscape=landscape,
        dashboard=dashboard,
        random_favs_browser=random_favs_browser,
    )


@dataclass(frozen=True)
class SecondaryMonitorRects:
    portrait: WindowRect
    main: WindowRect


def secondary_monitor_rects(
    secondary_monitor: MonitorRect, layout_config: LayoutConfig, *, majority: Crown,
) -> SecondaryMonitorRects:
    most = int(secondary_monitor.height * clamp01(layout_config.main_top_ratio))
    top = most if majority is Crown.PORTRAIT else secondary_monitor.height - most
    x, y, width = secondary_monitor.x, secondary_monitor.y, secondary_monitor.width
    return SecondaryMonitorRects(
        portrait=WindowRect(x, y, width, top),
        main=WindowRect(x, y + top, width, secondary_monitor.height - top),
    )


def compute_main_media_rect(
    *,
    secondary_monitor: MonitorRect,
    layout_config: LayoutConfig,
) -> WindowRect:
    return secondary_monitor_rects(
        secondary_monitor, layout_config, majority=Crown.PORTRAIT).main
