"""Monitor geometry querying for the Python orchestrator.

Provides ``enumerate_monitors`` (ctypes) to get live monitor work areas,
``virtual_desktop_rect`` for the box they all sit inside, and
``get_logical_monitor_rects`` to assign them to main/secondary roles with
orientation correction.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os

from .window_layout import MonitorRect

MonitorInfo = MonitorRect


# GetSystemMetrics indices for the box every monitor sits inside.
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def virtual_desktop_rect() -> MonitorInfo | None:
    """The bounding box of every monitor together, or None if it cannot be read.

    What a window covering the WHOLE desktop is sized and placed by.  Through
    ``ctypes.windll`` like the enumeration above, not the loader, whose stand-in
    raises where this caller has an answer to fall back on.
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        metric = user32.GetSystemMetrics
        return MonitorInfo(
            x=metric(SM_XVIRTUALSCREEN),
            y=metric(SM_YVIRTUALSCREEN),
            width=metric(SM_CXVIRTUALSCREEN),
            height=metric(SM_CYVIRTUALSCREEN),
        )
    except (AttributeError, OSError):
        # AttributeError off Windows, where ctypes carries no windll at all.
        return None


def enumerate_monitors() -> list[MonitorInfo]:
    """Return work-area rectangles for all monitors via Win32 EnumDisplayMonitors.

    ``FUN_TIME_FAKE_MONITORS`` (``x,y,w,h;x,y,w,h``) overrides it outright, for
    the integration suite's hidden desktop: that reports ONE monitor, on which
    the real layout collapses every window onto one screen and the players
    legitimately overlap, so "is each player frontmost over its own rect?" had
    nothing true to assert.  Windows place fine at coordinates no display backs.
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
    primary_index: int,
    secondary_index: int,
) -> tuple[MonitorRect, MonitorRect]:
    """Assign monitors to main/secondary roles, correcting for orientation.

    The rules, one test each: landscape wins over portrait, else leftmost wins.
    The two indices are the 1-based monitor numbers the config carries.
    """
    if not monitors:
        raise ValueError("No monitors detected")

    def _clamp(idx: int) -> int:
        return max(0, min(len(monitors) - 1, idx - 1))

    configured_main = monitors[_clamp(primary_index)]
    configured_secondary = monitors[_clamp(secondary_index)]

    main_is_landscape = configured_main.width >= configured_main.height
    secondary_is_portrait = configured_secondary.width < configured_secondary.height

    if main_is_landscape and secondary_is_portrait:
        main, secondary = configured_main, configured_secondary
    elif not main_is_landscape and not secondary_is_portrait:
        # Swapped: secondary is actually landscape, main is portrait
        main, secondary = configured_secondary, configured_main
    # Same orientation — leftmost is main
    elif configured_main.x <= configured_secondary.x:
        main, secondary = configured_main, configured_secondary
    else:
        main, secondary = configured_secondary, configured_main

    return main, secondary
