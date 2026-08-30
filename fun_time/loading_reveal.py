"""The panel's wait behind the loading cover, and how it comes out of it.

The panel is topmost, so one that simply came up would flash above the cover.
It is realized without ever being shown, and reveals itself UNDER the cover
(:func:`win32.insert_below`) as startup reaches its last phase — not when the
cover goes, which is a second or more later.

No Qt: the window arrives as something with a ``show``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.overlay_progress import startup_still_building
from fun_time.win32 import (
    find_window_by_title,
    hide_own_window,
    insert_below,
    show_own_window,
)


class Showable(Protocol):
    """The one thing this needs of the window it reveals."""

    def show(self) -> None: ...


class LoadingReveal:
    """Whether the panel is still waiting, and what happens when it stops.

    Built before the native window exists, because the answer it reads decides
    more than this window: the notice feed starts held from the same answer, and
    two reads of the progress file could disagree.
    """

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self.deferred = startup_still_building(state_dir)
        self._routing_suppressed = self.deferred
        self._hwnd = 0
        self._window: Showable | None = None

    def attach(self, hwnd: int, window: Showable) -> None:
        """Take the realized window, and put it in the state startup needs.

        Deferred, it is hidden outright: a hidden window renders nothing, so
        there is no flash and no minimize animation to see.  Otherwise it is
        shown at once.
        """
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
        """Whether THIS restore edge is the one startup's minimize accounts for.

        True at most once, and never after a reveal — revealing from hidden
        fires no minimize->restore edge at all, so the reveal clears this
        itself rather than waiting for one.
        """
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
        # Resolved BEFORE anything is shown: the panel is placed under the
        # cover by the same call that reveals it, and needs its handle in hand.
        cover = find_window_by_title(LOADING_SCREEN_TITLE, exact=True)
        assert self._window is not None
        self._window.show()
        show_own_window(self._hwnd)
        insert_below(self._hwnd, cover)
