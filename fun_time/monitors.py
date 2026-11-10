from __future__ import annotations

import ctypes
import ctypes.wintypes
import os

from .dashboard_layout import Rect
from .win32 import Win32Rect

MonitorRect = Rect
MonitorInfo = MonitorRect


def enumerate_monitors() -> list[MonitorInfo]:
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
    configured_primary_number: int,
    configured_secondary_number: int,
) -> tuple[MonitorRect, MonitorRect]:
    if not monitors:
        raise ValueError("No monitors detected")

    def _nearest_monitor_numbered(counting_from_one: int) -> MonitorInfo:
        return monitors[max(0, min(len(monitors) - 1, counting_from_one - 1))]

    configured_primary = _nearest_monitor_numbered(configured_primary_number)
    configured_secondary = _nearest_monitor_numbered(configured_secondary_number)

    primary_is_landscape = configured_primary.width >= configured_primary.height
    secondary_is_portrait = configured_secondary.width < configured_secondary.height

    if primary_is_landscape and secondary_is_portrait:
        primary, secondary = configured_primary, configured_secondary
    elif not primary_is_landscape and not secondary_is_portrait:
        primary, secondary = configured_secondary, configured_primary
    elif configured_primary.x <= configured_secondary.x:
        primary, secondary = configured_primary, configured_secondary
    else:
        primary, secondary = configured_secondary, configured_primary

    return primary, secondary
