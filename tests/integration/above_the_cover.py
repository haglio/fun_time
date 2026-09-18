"""What sits above the loading cover, and for how long.

Sampled from a thread of its own, because the question is about milliseconds:
every raise a session makes while the cover is up inserts that window ABOVE the
cover, and Windows never tells a window it has been displaced, so how long
anything shows through the scrim is the gap between the raise and whatever puts
it back.

The arithmetic lives here rather than in the test that uses it because it is
where the measurement went wrong once: a stay used to be inferred from the gaps
between samples of one window, with any gap under 20ms read as the same stay —
and the cover answers a raise in 16ms, so two separate appearances either side
of the cover's own answer were reported as one stay twice as long.  It is unit
tested next door in ``tests/test_above_the_cover.py``.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
from dataclasses import dataclass

from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.win32 import find_window_by_title

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
_user32.GetWindow.restype = wt.HWND
_user32.IsWindowVisible.argtypes = [wt.HWND]
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
GW_HWNDPREV = 3

# How long a window may sit over the cover: two display frames at 60Hz.  The
# floor under this is one SetWindowPos — whatever puts a window back cannot run
# before the raise has happened — so the number cannot be zero, and the 200ms
# the cover's first defense took is six times it.
VISIBLE_MS = 34.0

POLL_S = 0.002

# A sample this long after the last one leaves a stretch nobody watched, so it
# ends the stay instead of being counted into it.  Measured on this machine: the
# gap between consecutive samples is 2.5ms and never reached 3.5ms, even with
# twice as many busy processes as the machine has cores.
_LOST_SAMPLE_MS = 6.0


@dataclass(frozen=True)
class Stay:
    """One unbroken stretch of one window sitting above the cover."""

    what: str
    ms: float

    def __str__(self) -> str:
        return f"{self.what} for {self.ms:.0f}ms"


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def stays_from(samples: list[tuple[float, int, str]]) -> list[Stay]:
    """Every unbroken stay in *samples*, which are (when, hwnd, title) in order.

    A sample naming nothing (hwnd 0) is the cover back on top, and ends whatever
    was above it — so an appearance is never joined to the next one across the
    cover's own answer, however close together the two are.
    """
    out: list[Stay] = []
    start = previous = 0.0
    above = (0, "")

    def close() -> None:
        if above[0]:
            out.append(Stay(f"{above[1]!r} (hwnd={above[0]})", (previous - start) * 1000))

    for when, hwnd, title in samples:
        unwatched = above[0] and (when - previous) * 1000 > _LOST_SAMPLE_MS
        if (hwnd, title) != above or unwatched:
            close()
            above, start = (hwnd, title), when
        previous = when
    close()
    return out


class AboveTheCover(threading.Thread):
    """Sample the window immediately above the cover for as long as it is up.

    Only the one directly above it, because that is one ``GetWindow`` call and
    can therefore run at 2ms — walking the whole z-order costs tens of
    milliseconds a sample, which is the very interval being measured.
    """

    def __init__(self, timeout_s: float = 240.0) -> None:
        super().__init__(daemon=True)
        self._timeout_s = timeout_s
        self.cover_was_up = False
        self.samples: list[tuple[float, int, str]] = []

    def run(self) -> None:
        deadline = time.monotonic() + self._timeout_s
        cover = 0
        while time.monotonic() < deadline:
            cover = find_window_by_title(LOADING_SCREEN_TITLE, exact=True)
            if cover:
                break
            time.sleep(0.01)
        if not cover:
            return
        self.cover_was_up = True
        while time.monotonic() < deadline and _user32.IsWindowVisible(cover):
            hwnd = _user32.GetWindow(cover, GW_HWNDPREV)
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
