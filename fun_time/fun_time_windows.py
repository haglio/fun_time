from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app_support.win32 import window_exists

from . import preview_marker
from .origenerator_switches import RfbMode
from .win32 import (
    activate_window,
    find_window_by_pid,
    find_window_by_title,
    find_window_for_process,
    is_window_minimized,
    is_window_topmost,
    minimize_window,
    place_window,
    restore_window,
    set_always_on_top,
    window_process,
    window_rect,
)
from .window_layout import SecondaryMonitorRects, WindowRect
from .window_names import (
    FIXED_TOPMOST_WINDOWS,
    MANAGED_WINDOWS,
    ORIGENERATOR_WINDOW,
    RFB_MODE_WINDOWS,
    is_origenerators_caption,
    window_is_topmost,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

logger = logging.getLogger(__name__)


MAIN_BLANK_SETTLE_S = 0.25


def find_origenerators_window(pid: int) -> int:
    return find_window_for_process(pid, is_origenerators_caption, include_hidden=True)


@dataclass(frozen=True)
class ChildPids:
    main_funestra: int = 0
    portrait: int = 0
    landscape: int = 0
    dashboard: int = 0
    origenerator: int = 0


class FunTimeWindows:
    def __init__(
        self,
        *,
        pids: ChildPids,
        rfb_hwnd: int = 0,
        hwnds_by_window: dict[str, int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.pids = pids
        self.rfb_hwnd = rfb_hwnd
        self._hwnds_kept_for_when_hidden: dict[str, int] = dict(hwnds_by_window or {})
        self.clock = clock
        self._pending_hides: dict[str, float] = {}
        self._parked_hwnds: list[int] = []
        self._minimized_hwnds: list[int] = []

    def hwnd(self, window_name: str) -> int:
        hwnd = self._hwnds_kept_for_when_hidden.get(window_name, 0)
        if hwnd and window_name == ORIGENERATOR_WINDOW and not window_exists(hwnd):
            self._hwnds_kept_for_when_hidden.pop(window_name, None)
            hwnd = 0
        if hwnd:
            return hwnd
        if window_name == "main_funestra":
            hwnd = find_window_by_pid(self.pids.main_funestra) or find_window_by_title("Main Funestra", exact=True)
        elif window_name == "portrait":
            hwnd = (find_window_by_pid(self.pids.portrait)
                    or find_window_by_title(SATELLITE_PORTRAIT_TITLE, exact=True))
        elif window_name == "landscape":
            hwnd = (find_window_by_pid(self.pids.landscape)
                    or find_window_by_title(SATELLITE_LANDSCAPE_TITLE, exact=True))
        elif window_name == "dashboard":
            hwnd = self._dashboard_hwnd()
        elif window_name == "rfb":
            hwnd = self.rfb_hwnd
        elif window_name == ORIGENERATOR_WINDOW:
            hwnd = find_origenerators_window(self.pids.origenerator)
        if hwnd:
            self._hwnds_kept_for_when_hidden[window_name] = hwnd
        return hwnd

    def _dashboard_hwnd(self) -> int:
        hwnd = find_window_by_pid(self.pids.dashboard) if self.pids.dashboard else 0
        if not hwnd:
            hwnd = find_window_by_title(
                preview_marker.app_title(preview_marker.shown_as()), exact=True)
            if hwnd:
                logger.info(
                    "Dashboard found by title (hwnd=%d) but NOT by pid %d",
                    hwnd, self.pids.dashboard,
                )
        return hwnd


    def show(self, window_name: str) -> None:
        self._pending_hides.pop(window_name, None)
        hwnd = self.hwnd(window_name)
        if hwnd:
            restore_window(hwnd, activate=False)

    def activate(self, window_name: str) -> None:
        hwnd = self.hwnd(window_name)
        if hwnd:
            activate_window(hwnd)

    def hide_after_settle(self, window_name: str) -> None:
        # Minimizing freezes a window's Alt-Tab thumbnail, since Windows stops compositing it, so
        # the window gets a frame or two first.
        self._pending_hides[window_name] = self.clock() + MAIN_BLANK_SETTLE_S

    def flush_pending_hides(self) -> None:
        if not self._pending_hides:
            return
        now = self.clock()
        for window_name in [r for r, due in self._pending_hides.items() if now >= due]:
            del self._pending_hides[window_name]
            self.minimize(window_name)

    def minimize(self, window_name: str) -> None:
        hwnd = self.hwnd(window_name)
        if hwnd:
            minimize_window(hwnd, activate=False)

    def park(self, window_name: str) -> None:
        hwnd = self.hwnd(window_name)
        if not hwnd:
            return
        self.minimize(window_name)
        if hwnd not in self._parked_hwnds:
            self._parked_hwnds.append(hwnd)

    def restore_parked(self) -> None:
        for hwnd in self._parked_hwnds:
            restore_window(hwnd, activate=False)
        self._parked_hwnds = []

    def minimize_all(self, window_names: Iterable[str]) -> None:
        self._minimized_hwnds = []
        for window_name in window_names:
            hwnd = self.hwnd(window_name)
            if hwnd:
                minimize_window(hwnd, activate=False)
                self._minimized_hwnds.append(hwnd)

    def restore_minimized(self) -> None:
        for hwnd in self._minimized_hwnds:
            restore_window(hwnd, activate=False)
        self._minimized_hwnds = []
        self._parked_hwnds = []

    def seat(self, rects: SecondaryMonitorRects) -> None:
        self.place([("portrait", rects.portrait), ("main_funestra", rects.main)])

    def takes_typing(self, window: int, process: int, *, hosted_app: bool) -> bool:
        if self.rfb_hwnd and window == self.rfb_hwnd:
            return True
        if not hosted_app or not process:
            return False
        hwnd = self.hwnd(ORIGENERATOR_WINDOW)
        return bool(hwnd) and process == window_process(hwnd)

    def place(self, placements: Iterable[tuple[str, WindowRect]]) -> None:
        moves = []
        for window_name, rect in placements:
            hwnd = self.hwnd(window_name)
            if not hwnd or is_window_minimized(hwnd):
                continue
            now = window_rect(hwnd)
            if now != (rect.x, rect.y, rect.width, rect.height):
                growth = rect.width * rect.height - (now[2] * now[3] if now else 0)
                moves.append((growth, hwnd, rect))
        for _growth, hwnd, rect in sorted(moves, key=lambda move: move[0]):
            place_window(hwnd, rect.x, rect.y, rect.width, rect.height)


    def remove_all_topmost(self) -> None:
        for window_name in MANAGED_WINDOWS:
            hwnd = self.hwnd(window_name)
            if hwnd:
                set_always_on_top(hwnd, False)

    def restore_all_topmost(self, rfb_mode: RfbMode) -> None:
        for window_name in FIXED_TOPMOST_WINDOWS:
            hwnd = self.hwnd(window_name)
            if hwnd:
                set_always_on_top(hwnd, True)
        self.restack_rfb_mode(rfb_mode)

    def restack_rfb_mode(self, rfb_mode: RfbMode, *, paused: bool = False) -> None:
        if paused:
            return
        browser = self.hwnd("rfb")
        if browser:
            set_always_on_top(browser, window_is_topmost("rfb", rfb_mode))
        if not window_is_topmost(ORIGENERATOR_WINDOW, rfb_mode):
            return
        hwnd = self.hwnd(ORIGENERATOR_WINDOW)
        if hwnd:
            set_always_on_top(hwnd, True)

    def converge_origenerator_window(self, rfb_mode: RfbMode) -> None:
        if not self.pids.origenerator:
            return
        hwnd = self.hwnd(ORIGENERATOR_WINDOW)
        if not hwnd:
            return
        minimized = is_window_minimized(hwnd)
        if rfb_mode is RfbMode.ORIGENERATOR:
            if minimized:
                restore_window(hwnd, activate=False)
            if minimized or self._an_rfb_mode_window_out_of_its_band(rfb_mode):
                self.restack_rfb_mode(rfb_mode)
        elif not minimized:
            minimize_window(hwnd, activate=False)

    def _an_rfb_mode_window_out_of_its_band(self, rfb_mode: RfbMode) -> bool:
        return any(is_window_topmost(hwnd) != window_is_topmost(window_name, rfb_mode)
                   for window_name in RFB_MODE_WINDOWS if (hwnd := self.hwnd(window_name)))

    def topmost_report(self) -> str:
        parts = []
        for window_name in MANAGED_WINDOWS:
            hwnd = self.hwnd(window_name)
            state = is_window_topmost(hwnd) if hwnd else "n/a"
            parts.append(f"{window_name}={hwnd}:{state}")
        return "  ".join(parts)
