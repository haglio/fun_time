from __future__ import annotations

from pathlib import Path
from typing import Protocol

from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.overlay_progress import startup_still_building
from fun_time.win32 import (
    find_window_by_title,
    hide_own_window,
    set_always_on_top,
    show_own_window,
)


class Showable(Protocol):
    def show(self) -> None: ...


class LoadingReveal:
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self.deferred = startup_still_building(state_dir)
        self._routing_suppressed = self.deferred
        self._hwnd = 0
        self._window: Showable | None = None

    def attach(self, hwnd: int, window: Showable) -> None:
        self._hwnd, self._window = hwnd, window
        if self.deferred:
            hide_own_window(hwnd)
        else:
            window.show()
            show_own_window(hwnd)

    @property
    def routing_suppressed(self) -> bool:
        return self._routing_suppressed

    def took_the_reveals_own_restore(self) -> bool:
        if not self._routing_suppressed:
            return False
        self._routing_suppressed = False
        return True

    def maybe_reveal(self) -> None:
        if not self.deferred or startup_still_building(self._state_dir):
            return
        self.deferred = False
        self._routing_suppressed = False
        assert self._window is not None
        loading_screen = _the_loading_screen_found_before_anything_moves()
        if loading_screen:
            self._reveal_beneath(loading_screen)
        else:
            self._reveal_where_it_sits()

    def _reveal_beneath(self, loading_screen: int) -> None:
        self._leave_the_band_a_shown_window_tops()
        self._reveal_where_it_sits()
        set_always_on_top(self._hwnd, True, under=loading_screen)

    def _leave_the_band_a_shown_window_tops(self) -> None:
        set_always_on_top(self._hwnd, False)

    def _reveal_where_it_sits(self) -> None:
        assert self._window is not None
        self._window.show()
        show_own_window(self._hwnd)


def _the_loading_screen_found_before_anything_moves() -> int:
    return find_window_by_title(LOADING_SCREEN_TITLE, exact=True)
