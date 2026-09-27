from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from .chrome_accessibility import open_chrome_window
from .chrome_tabs import ChromeTabSwitcher
from .win32 import (
    foreground_window,
    is_window_minimized,
    seconds_since_input,
    window_exists,
    window_under_cursor,
)

SLIDESHOW_INTERVAL_S = 10.0
STILL_TYPING_S = 1.0


@dataclass(frozen=True)
class RfbGlance:
    showing: bool = True
    under_cursor: bool = False
    foreground: bool = False
    idle_s: float = float("inf")


def glance_at(hwnd: int) -> RfbGlance:
    return RfbGlance(
        showing=window_exists(hwnd) and not is_window_minimized(hwnd),
        under_cursor=window_under_cursor() == hwnd,
        foreground=foreground_window() == hwnd,
        idle_s=seconds_since_input(),
    )


class TabSwitcher(Protocol):
    def show_next_tab(self) -> None: ...

    def stop(self) -> None: ...


class RfbSlideshow:
    def __init__(self, *, tabs: TabSwitcher, glance: Callable[[], RfbGlance]) -> None:
        self._tabs = tabs
        self._glance = glance
        self._due_at: float | None = None
        self._clicked_into = False

    def restart(self, *, now: float) -> None:
        self._due_at = now + SLIDESHOW_INTERVAL_S

    def tick(self, *, now: float, held: bool) -> None:
        glance = self._glance()
        self._clicked_into = glance.foreground and (self._clicked_into or glance.under_cursor)
        if self._due_at is None or held or not glance.showing or self._in_use(glance):
            self.restart(now=now)
        elif now >= self._due_at:
            self._tabs.show_next_tab()
            self.restart(now=now)

    def stop(self) -> None:
        self._tabs.stop()

    def _in_use(self, glance: RfbGlance) -> bool:
        return glance.under_cursor or (self._clicked_into and glance.idle_s < STILL_TYPING_S)


def rfb_slideshow_on(hwnd: int) -> RfbSlideshow:
    return RfbSlideshow(tabs=ChromeTabSwitcher(hwnd, open_window=open_chrome_window),
                        glance=partial(glance_at, hwnd))
