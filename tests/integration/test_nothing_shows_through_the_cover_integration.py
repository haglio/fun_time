"""Nothing shows through the loading cover for long enough to see.

His: "many things flash out for a split second from underneath the loading
screen's scrim."  Nothing keeps a topmost window above the OTHER topmost
windows — every raise a session makes while the cover is up (showing a player,
moving it onto its rect, promoting it into the band) inserts that window ABOVE
the cover, and Windows never tells a window it has been displaced.  The cover's
only defense is taking the top back, so how fast it does that IS how long a
window shows through it.  It used to do that once every 200ms, and only then,
which is a fifth of a second of a player visible through the scrim — a split
second, once per raise, and there is a raise for every window in the room.

So this watches the window immediately above the cover, at 2ms, for the whole
time the cover is up, and asks how LONG anything managed to stay there.  Not
whether anything ever got above it: a promotion and the cover's answer to it are
two SetWindowPos calls and the gap between them is real, so the only truthful
question is whether that gap is short enough that no frame is ever drawn from
it.  One display frame is 16.7ms at 60Hz; the budget here is two, and the
behavior this replaces would blow it by an order of magnitude.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import shutil
import sys
import threading
import time

import pytest

from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.win32 import find_window_by_title

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
_user32.GetWindow.restype = wt.HWND
_user32.IsWindowVisible.argtypes = [wt.HWND]
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
GW_HWNDPREV = 3

# How long a window may sit over the cover: two display frames at 60Hz.  The
# floor under this is one SetWindowPos — the cover cannot answer a promotion
# before the promotion has happened — so the number cannot be zero, and the
# 200ms it replaces is twelve times it.
VISIBLE_MS = 34.0

# Samples closer together than this belong to the same stay.  Comfortably above
# the 2ms poll and below the budget, so a stay is never split and two separate
# ones are never merged.
_SAME_STAY_MS = 20.0
_POLL_S = 0.002


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


class _AboveTheCover(threading.Thread):
    """Sample the window immediately above the cover for as long as it is up.

    Only the one directly above it, because that is one ``GetWindow`` call and
    can therefore run at 2ms — walking the whole z-order costs tens of
    milliseconds a sample, which is the very interval being measured.
    """

    def __init__(self, timeout_s: float = 240.0) -> None:
        super().__init__(daemon=True)
        self._timeout_s = timeout_s
        self.cover_was_up = False
        self.samples = 0
        self.seen: dict[tuple[int, str], list[float]] = {}

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
            self.samples += 1
            hwnd = _user32.GetWindow(cover, GW_HWNDPREV)
            while hwnd and not _user32.IsWindowVisible(hwnd):
                hwnd = _user32.GetWindow(hwnd, GW_HWNDPREV)
            if hwnd:
                key = (int(hwnd), _window_title(int(hwnd)))
                self.seen.setdefault(key, []).append(time.monotonic())
            time.sleep(_POLL_S)

    def stays(self) -> list[tuple[str, float]]:
        """Each unbroken stay above the cover, as (what it was, how long in ms)."""
        out: list[tuple[str, float]] = []
        for (hwnd, title), stamps in self.seen.items():
            start = previous = stamps[0]
            for stamp in stamps[1:]:
                if (stamp - previous) * 1000 > _SAME_STAY_MS:
                    out.append((f"{title!r} (hwnd={hwnd})", (previous - start) * 1000))
                    start = stamp
                previous = stamp
            out.append((f"{title!r} (hwnd={hwnd})", (previous - start) * 1000))
        return out


def test_nothing_stays_over_the_cover_long_enough_to_be_seen():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    watcher = _AboveTheCover()
    watcher.start()
    try:
        # With the dashboard, because it and its notice overlay are two of the
        # topmost windows that can land over the cover.  Faked side-by-side
        # monitors for the reason the other overlay tests fake them: on the
        # hidden desktop's single screen the real layout collapses every window
        # onto it.
        session.start(wait_seconds=180.0, env_overrides={
            "FUN_TIME_INTEGRATION_OVERLAYS": "1",
            "FUN_TIME_DISABLE_DASHBOARD": "0",
            "FUN_TIME_FAKE_MONITORS": "0,0,1280,720;1280,0,720,1440",
        })
        watcher.join(timeout=60.0)

        assert watcher.cover_was_up, "the cover never appeared"
        assert watcher.samples > 100, (
            f"only {watcher.samples} samples: the cover was barely up, so this "
            "measured nothing"
        )
        too_long = [(what, ms) for what, ms in watcher.stays() if ms > VISIBLE_MS]
        assert not too_long, (
            "these sat over the cover long enough to be drawn: "
            + "; ".join(f"{what} for {ms:.0f}ms" for what, ms in too_long)
        )
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)
