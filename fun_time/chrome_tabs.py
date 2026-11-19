from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from app_support.threading_utils import start_daemon_thread

logger = logging.getLogger(__name__)

TAB_STRIP_ATTEMPTS = 6
RETRY_AFTER_S = 0.5
STOP_WAIT_S = 2.0
STARTUP_WATCH_S = 60.0
LOOK_FOR_EXTRA_TABS_EVERY_S = 1.0


class AccessibleNode(Protocol):
    is_tab: bool
    is_page: bool
    is_active: bool

    def children(self) -> Sequence[AccessibleNode]: ...

    def switch_to(self) -> None: ...

    def close(self) -> None: ...


def find_tab_strip(window: AccessibleNode) -> AccessibleNode | None:
    unwalked = [window]
    while unwalked:
        node = unwalked.pop()
        if node.is_page:
            continue
        children = node.children()
        if any(child.is_tab for child in children):
            return node
        unwalked.extend(reversed(children))
    return None


def tab_after_the_active_one(tabs: Sequence[AccessibleNode]) -> AccessibleNode | None:
    if len(tabs) <= 1:
        return None
    active = next((index for index, tab in enumerate(tabs) if tab.is_active), -1)
    return tabs[(active + 1) % len(tabs)]


def switch_to_the_tab_after_the_active_one(strip: AccessibleNode) -> None:
    next_tab = tab_after_the_active_one([child for child in strip.children() if child.is_tab])
    if next_tab is not None:
        next_tab.switch_to()


def close_the_tabs_past_the_browsers_own(strip: AccessibleNode, *, own_tabs: int) -> int:
    not_its_own = [child for child in strip.children() if child.is_tab][own_tabs:]
    for tab in reversed(not_its_own):
        tab.close()
    return len(not_its_own)


class StartupTabCloser:
    def __init__(
        self,
        hwnd: int,
        *,
        own_tabs: int,
        open_window: Callable[[int], AccessibleNode],
        watch_s: float = STARTUP_WATCH_S,
        look_every_s: float = LOOK_FOR_EXTRA_TABS_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hwnd = hwnd
        self._own_tabs = own_tabs
        self._open_window = open_window
        self._watch_s = watch_s
        self._look_every_s = look_every_s
        self._clock = clock
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = start_daemon_thread(target=self.watch, name="rfb-startup-tabs")

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=STOP_WAIT_S)

    def watch(self) -> None:
        watch_ends_at = self._clock() + self._watch_s
        strip = None
        while not self._stopping.is_set() and self._clock() < watch_ends_at:
            try:
                strip = strip or find_tab_strip(self._open_window(self._hwnd))
                if strip is not None:
                    self._close_extra_tabs_on(strip)
            except Exception:
                strip = None
            self._stopping.wait(self._look_every_s)

    def _close_extra_tabs_on(self, strip: AccessibleNode) -> None:
        closed = close_the_tabs_past_the_browsers_own(strip, own_tabs=self._own_tabs)
        if closed:
            logger.info("Closed %d tab(s) the Random Faves Browser did not open itself", closed)


class ChromeTabSwitcher:
    def __init__(
        self,
        hwnd: int,
        *,
        open_window: Callable[[int], AccessibleNode],
        retry_after_s: float = RETRY_AFTER_S,
    ) -> None:
        self._hwnd = hwnd
        self._open_window = open_window
        self._retry_after_s = retry_after_s
        self._asked = threading.Event()
        self._stopping = threading.Event()
        self._refused = False
        self._thread: threading.Thread | None = None

    def show_next_tab(self) -> None:
        if self._thread is None:
            self._thread = start_daemon_thread(target=self._serve, name="rfb-slideshow")
        self._asked.set()

    def stop(self) -> None:
        self._stopping.set()
        self._asked.set()
        if self._thread is not None:
            self._thread.join(timeout=STOP_WAIT_S)

    def _serve(self) -> None:
        strip = None
        while self._wait_to_be_asked():
            strip = self._switch_on(strip)
        logger.info("RFB slideshow stopped")

    def _wait_to_be_asked(self) -> bool:
        self._asked.wait()
        self._asked.clear()
        return not self._stopping.is_set()

    def _switch_on(self, strip: AccessibleNode | None) -> AccessibleNode | None:
        for _attempt in range(TAB_STRIP_ATTEMPTS):
            try:
                if strip is None:
                    strip = find_tab_strip(self._open_window(self._hwnd))
                if strip is not None:
                    switch_to_the_tab_after_the_active_one(strip)
                    self._answered()
                    return strip
            except Exception as reason:
                self._refused_with(reason)
                strip = None
            if self._stopping.wait(self._retry_after_s):
                return None
        self._refused_with(f"no tab strip in {TAB_STRIP_ATTEMPTS} looks at the window")
        return None

    def _refused_with(self, reason: object) -> None:
        if not self._refused:
            logger.warning("RFB slideshow could not reach the browser's tabs: %s", reason)
        self._refused = True

    def _answered(self) -> None:
        if self._refused:
            logger.info("RFB slideshow reached the browser's tabs again")
        self._refused = False
