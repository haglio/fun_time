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
    landscape: WindowRect
    dashboard: WindowRect
    log_panel: WindowRect
    random_favs_browser: WindowRect


def compute_window_layout(
    *,
    main_monitor: MonitorRect,
    secondary_monitor: MonitorRect,
    layout_config: LayoutConfig,
) -> WindowLayoutPlan:
    dashboard_size = compute_dashboard_size(
        main_monitor=main_monitor,
        secondary_monitor=secondary_monitor,
        layout_config=layout_config,
    )
    portrait_height = int(secondary_monitor.height * clamp01(layout_config.primary_top_ratio))

    portrait = WindowRect(
        x=secondary_monitor.x,
        y=secondary_monitor.y,
        width=secondary_monitor.width,
        height=portrait_height,
    )

    landscape_width = int(main_monitor.width * clamp01(layout_config.landscape_width_ratio))
    landscape = WindowRect(
        x=main_monitor.x + (main_monitor.width - landscape_width),
        y=main_monitor.y,
        width=landscape_width,
        height=main_monitor.height,
    )

    # The left column stacks a dashboard+log-panel row above the RFB.  The
    # dashboard keeps its natural size in the column's top-left corner and the
    # log panel takes the strip beside it, so the two together span the column
    # width.  The RFB then fills the whole rectangle from their shared bottom
    # edge down to the monitor's bottom edge.
    left_width = main_monitor.width - landscape_width
    dashboard = WindowRect(
        x=main_monitor.x,
        y=main_monitor.y,
        width=dashboard_size.width,
        height=dashboard_size.height,
    )
    log_panel = WindowRect(
        x=dashboard.x + dashboard.width,
        y=main_monitor.y,
        width=left_width - dashboard_size.width,
        height=dashboard_size.height,
    )
    random_favs_browser = WindowRect(
        x=main_monitor.x,
        y=main_monitor.y + dashboard_size.height,
        width=left_width,
        height=main_monitor.height - dashboard_size.height,
    )

    return WindowLayoutPlan(
        portrait=portrait,
        landscape=landscape,
        dashboard=dashboard,
        log_panel=log_panel,
        random_favs_browser=random_favs_browser,
    )


def compute_primary_media_rect(
    *,
    secondary_monitor: MonitorRect,
    layout_config: LayoutConfig,
) -> WindowRect:
    """The primary display slot Nau and Genau share.

    The portrait satellite takes the top ``primary_top_ratio`` of the secondary
    monitor; the primary player fills the rest below it.  Startup positions Nau
    and Genau here, and the notice overlay flashes primary notices here, so both
    derive it from this one function.
    """
    portrait_height = int(secondary_monitor.height * clamp01(layout_config.primary_top_ratio))
    return WindowRect(
        x=secondary_monitor.x,
        y=secondary_monitor.y + portrait_height,
        width=secondary_monitor.width,
        height=secondary_monitor.height - portrait_height,
    )


def compute_dashboard_size(
    *,
    main_monitor: MonitorRect,
    secondary_monitor: MonitorRect,
    layout_config: LayoutConfig,
) -> Size:
    """The dashboard's natural (fixed-scale) scene size.

    Must match the scene ``build_dashboard_window`` renders, so both derive it
    from the same :func:`compute_dashboard_preview_layout`.
    """
    preview = compute_dashboard_preview_layout(
        Size(main_monitor.width, main_monitor.height),
        Size(secondary_monitor.width, secondary_monitor.height),
        layout_config,
    )
    return Size(preview.dashboard_width, preview.dashboard_height)

