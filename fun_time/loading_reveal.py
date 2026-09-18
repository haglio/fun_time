"""The panel's wait under the loading cover, and how it comes out of it.

Realized without being shown, and revealed at startup's last phase rather than
when the cover goes a second or more later.  No Qt: it arrives as something
with a ``show``.
"""
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
    """The one thing this needs of the window it reveals."""

    def show(self) -> None: ...


class LoadingReveal:
    """Whether the panel is still waiting, and what happens when it stops.
    Built before the native window exists: the notice feed starts held from the
    same answer, and two reads of the progress file could disagree."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self.deferred = startup_still_building(state_dir)
        self._routing_suppressed = self.deferred
        self._hwnd = 0
        self._window: Showable | None = None

    def attach(self, hwnd: int, window: Showable) -> None:
        """Take the realized window and put it in the state startup needs;
        deferred, it is hidden outright, so no flash and no animation."""
        self._hwnd, self._window = hwnd, window
        if self.deferred:
            hide_own_window(hwnd)
        else:
            window.show()
            show_own_window(hwnd)

    @property
    def routing_suppressed(self) -> bool:
        """Whether a minimize edge right now is startup's rather than a gesture."""
        return self._routing_suppressed

    def took_the_first_restore(self) -> bool:
        """Whether THIS restore edge is startup's own.  True at most once."""
        if not self._routing_suppressed:
            return False
        self._routing_suppressed = False
        return True

    def maybe_reveal(self) -> None:
        """Show the panel if startup has reached its last phase."""
        if not self.deferred or startup_still_building(self._state_dir):
            return
        self.deferred = False
        self._routing_suppressed = False
        assert self._window is not None
        cover = find_window_by_title(LOADING_SCREEN_TITLE, exact=True)
        if cover:
            self._reveal_beneath(cover)
        else:
            self._reveal_where_it_sits()

    def _reveal_beneath(self, cover: int) -> None:
        """Out of the topmost band before the panel is visible, back into it
        directly under the cover once it is.

        Between the two it is beneath the cover by Windows' own rule — a window
        that is not topmost cannot be above one that is — rather than by
        winning a race with whatever would otherwise have to put it back.
        """
        set_always_on_top(self._hwnd, False)
        self._reveal_where_it_sits()
        set_always_on_top(self._hwnd, True, under=cover)

    def _reveal_where_it_sits(self) -> None:
        assert self._window is not None
        self._window.show()
        show_own_window(self._hwnd)
