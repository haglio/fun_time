from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
from dataclasses import dataclass

from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.win32 import find_window_by_title

from .run_clock import Budget

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
_user32.GetWindow.restype = wt.HWND
_user32.IsWindowVisible.argtypes = [wt.HWND]
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
GW_HWNDPREV = 3

_FRAME_AT_60HZ_MS = 17.0
VISIBLE_MS = 2 * _FRAME_AT_60HZ_MS

POLL_S = 0.002

_LONGEST_GAP_COUNTED_AS_WATCHED_MS = 6.0


@dataclass(frozen=True)
class Stay:
    what: str
    ms: float

    def __str__(self) -> str:
        return f"{self.what} for {self.ms:.0f}ms"


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def stays_from(samples: list[tuple[float, int, str]]) -> list[Stay]:
    out: list[Stay] = []
    start = previous = 0.0
    above = (0, "")

    def close() -> None:
        if above[0]:
            out.append(Stay(f"{above[1]!r} (hwnd={above[0]})", (previous - start) * 1000))

    for when, hwnd, title in samples:
        unwatched = above[0] and (when - previous) * 1000 > _LONGEST_GAP_COUNTED_AS_WATCHED_MS
        if (hwnd, title) != above or unwatched:
            close()
            above, start = (hwnd, title), when
        previous = when
    close()
    return out


class AboveTheLoadingScreen(threading.Thread):
    def __init__(self, timeout_s: float = 240.0) -> None:
        super().__init__(daemon=True)
        self._timeout_s = timeout_s
        self.loading_screen_was_up = False
        self.samples: list[tuple[float, int, str]] = []

    def run(self) -> None:
        budget = Budget(self._timeout_s)
        loading_screen = 0
        while not budget.expired():
            loading_screen = find_window_by_title(LOADING_SCREEN_TITLE, exact=True)
            if loading_screen:
                break
            time.sleep(0.01)
        if not loading_screen:
            return
        self.loading_screen_was_up = True
        while not budget.expired() and _user32.IsWindowVisible(loading_screen):
            hwnd = _user32.GetWindow(loading_screen, GW_HWNDPREV)
            while hwnd and not _user32.IsWindowVisible(hwnd):
                hwnd = _user32.GetWindow(hwnd, GW_HWNDPREV)
            hwnd = int(hwnd or 0)
            self.samples.append(
                (time.monotonic(), hwnd, _window_title(hwnd) if hwnd else ""))
            time.sleep(POLL_S)

    def stays(self) -> list[Stay]:
        return stays_from(self.samples)

    def too_long(self) -> list[str]:
        return [str(stay) for stay in self.stays() if stay.ms > VISIBLE_MS]
