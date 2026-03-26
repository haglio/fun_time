"""Monitor geometry querying for the Python orchestrator.

Provides ``enumerate_monitors`` (ctypes) to get live monitor work areas,
and ``get_logical_monitor_rects`` to assign them to main/secondary roles
using the same orientation-correction logic as the AHK bridge.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from dataclasses import dataclass

from .windows_bridge_window_layout import MonitorRect


@dataclass(frozen=True)
class MonitorInfo:
    x: int
    y: int
    width: int
    height: int


def enumerate_monitors() -> list[MonitorInfo]:
    """Return work-area rectangles for all monitors via Win32 EnumDisplayMonitors."""
    monitors: list[MonitorInfo] = []

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("rcMonitor", ctypes.wintypes.RECT),
            ("rcWork", ctypes.wintypes.RECT),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szDevice", ctypes.wintypes.WCHAR * 32),
        ]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HMONITOR,
        ctypes.wintypes.HDC,
        ctypes.POINTER(ctypes.wintypes.RECT),
        ctypes.wintypes.LPARAM,
    )

    def callback(hmonitor, _hdc, _lprect, _lparam):  # type: ignore[no-untyped-def]
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info))
        rc = info.rcWork
        monitors.append(MonitorInfo(
            x=rc.left,
            y=rc.top,
            width=rc.right - rc.left,
            height=rc.bottom - rc.top,
        ))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(
        None, None, MONITORENUMPROC(callback), 0,
    )
    return monitors


def get_logical_monitor_rects(
    monitors: list[MonitorInfo],
    *,
    main_index: int,
    secondary_index: int,
) -> tuple[MonitorRect, MonitorRect]:
    """Assign monitors to main/secondary roles with orientation correction.

    Replicates the AHK ``GetLogicalMonitorRects`` logic:
    - If one monitor is landscape and the other portrait, landscape is main.
    - If both have the same orientation, the leftmost is main.

    ``main_index`` and ``secondary_index`` are 1-based monitor numbers from config.
    """
    if not monitors:
        raise ValueError("No monitors detected")

    def _clamp(idx: int) -> int:
        return max(0, min(len(monitors) - 1, idx - 1))

    configured_main = monitors[_clamp(main_index)]
    configured_secondary = monitors[_clamp(secondary_index)]

    main_is_landscape = configured_main.width >= configured_main.height
    secondary_is_portrait = configured_secondary.width < configured_secondary.height

    if main_is_landscape and secondary_is_portrait:
        main, secondary = configured_main, configured_secondary
    elif not main_is_landscape and not secondary_is_portrait:
        # Swapped: secondary is actually landscape, main is portrait
        main, secondary = configured_secondary, configured_main
    else:
        # Same orientation — leftmost is main
        if configured_main.x <= configured_secondary.x:
            main, secondary = configured_main, configured_secondary
        else:
            main, secondary = configured_secondary, configured_main

    return (
        MonitorRect(main.x, main.y, main.width, main.height),
        MonitorRect(secondary.x, secondary.y, secondary.width, secondary.height),
    )
