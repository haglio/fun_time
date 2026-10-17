"""Monitor geometry querying for the Python orchestrator.

Provides ``enumerate_monitors`` (ctypes) to get live monitor work areas, and
``get_logical_monitor_rects`` to assign them to main/secondary roles with
orientation correction.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os

from .dashboard_layout import Rect
from .win32_loader import Win32Rect

MonitorRect = Rect
MonitorInfo = MonitorRect


def enumerate_monitors() -> list[MonitorInfo]:
    """Return work-area rectangles for all monitors via Win32 EnumDisplayMonitors.

    ``FUN_TIME_FAKE_MONITORS`` (``x,y,w,h;x,y,w,h``) overrides it outright: an
    integration session lays its room out on the same two monitors whatever the
    machine lists.  Windows place fine at coordinates no display backs.
    """
    fake = os.environ.get("FUN_TIME_FAKE_MONITORS", "").strip()
    if fake:
        return [
            MonitorInfo(x=int(x), y=int(y), width=int(w), height=int(h))
            for x, y, w, h in (part.split(",") for part in fake.split(";") if part)
        ]
    monitors: list[MonitorInfo] = []

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("rcMonitor", Win32Rect),
            ("rcWork", Win32Rect),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szDevice", ctypes.wintypes.WCHAR * 32),
        ]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HMONITOR,
        ctypes.wintypes.HDC,
        ctypes.POINTER(Win32Rect),
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
            height=rc.lower - rc.top,
        ))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(
        None, None, MONITORENUMPROC(callback), 0,
    )
    return monitors


def get_logical_monitor_rects(
    monitors: list[MonitorInfo],
    *,
    primary_index: int,
    secondary_index: int,
) -> tuple[MonitorRect, MonitorRect]:
    """Assign monitors to the primary and secondary roles, correcting for orientation.

    The rules, one test each: landscape wins over portrait, else leftmost wins.
    The two indices are the 1-based monitor numbers the config carries, and
    "primary" is its word: not the Main Funestra's monitor, which is the secondary.
    """
    if not monitors:
        raise ValueError("No monitors detected")

    def _clamp(idx: int) -> int:
        return max(0, min(len(monitors) - 1, idx - 1))

    configured_primary = monitors[_clamp(primary_index)]
    configured_secondary = monitors[_clamp(secondary_index)]

    primary_is_landscape = configured_primary.width >= configured_primary.height
    secondary_is_portrait = configured_secondary.width < configured_secondary.height

    if primary_is_landscape and secondary_is_portrait:
        primary, secondary = configured_primary, configured_secondary
    elif not primary_is_landscape and not secondary_is_portrait:
        # Swapped: the secondary is actually landscape, the primary portrait
        primary, secondary = configured_secondary, configured_primary
    # Same orientation — leftmost is the primary
    elif configured_primary.x <= configured_secondary.x:
        primary, secondary = configured_primary, configured_secondary
    else:
        primary, secondary = configured_secondary, configured_primary

    return primary, secondary
