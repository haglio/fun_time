from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from app_support.threading_utils import wait_until

from fun_time.chrome_tabs import (
    TAB_STRIP_ATTEMPTS,
    ChromeTabSwitcher,
    StartupTabCloser,
    close_the_tabs_past_the_browsers_own,
    find_tab_strip,
    tab_after_the_active_one,
)

RFB_HWND = 7777


@dataclass(eq=False)
class FakeNode:
    name: str = ""
    is_tab: bool = False
    is_page: bool = False
    is_active: bool = False
    kids: list[FakeNode] = field(default_factory=list)
    walked_into: bool = False
    on_switch: Callable[[FakeNode], None] | None = None
    on_close: Callable[[FakeNode], None] | None = None

    def children(self) -> list[FakeNode]:
        self.walked_into = True
        return list(self.kids)

    def switch_to(self) -> None:
        assert self.on_switch is not None
        self.on_switch(self)

    def close(self) -> None:
        assert self.on_close is not None
        self.on_close(self)


def _tabs(*names: str, active: str) -> list[FakeNode]:
    return [FakeNode(name, is_tab=True, is_active=name == active) for name in names]


def test_the_tab_after_the_active_one_is_next():
    tabs = _tabs("a", "b", "c", active="a")

    assert tab_after_the_active_one(tabs).name == "b"


def test_the_last_tab_wraps_round_to_the_first():
    tabs = _tabs("a", "b", "c", active="c")

    assert tab_after_the_active_one(tabs).name == "a"


def test_with_no_tab_reading_as_active_the_first_one_is_next():
    tabs = _tabs("a", "b", "c", active="")

    assert tab_after_the_active_one(tabs).name == "a"


def test_a_lone_tab_has_nothing_after_it():
    assert tab_after_the_active_one(_tabs("a", active="a")) is None


def test_the_tab_strip_is_found_without_walking_into_any_page():
    page = FakeNode("page", is_page=True, kids=[FakeNode("a link that is a tab", is_tab=True)])
    strip = FakeNode("strip", kids=[*_tabs("a", "b", active="a"), FakeNode("new tab button")])
    window = FakeNode("window", kids=[FakeNode("frame", kids=[page, FakeNode("toolbar", kids=[strip])])])

    assert find_tab_strip(window) is strip
    assert not page.walked_into


class FakeChrome:
    def __init__(self, *names: str, active: str) -> None:
        self.tabs = _tabs(*names, active=active)
        for tab in self.tabs:
            tab.on_switch = self._switch
            tab.on_close = self._close
        self.strip = FakeNode("strip", kids=self.tabs)
        self.window = FakeNode("window", kids=[FakeNode("toolbar", kids=[self.strip])])
        self.opened_on: list[threading.Thread] = []
        self.switched: list[str] = []
        self.closed: list[str] = []

    def open_window(self, hwnd: int) -> FakeNode:
        assert hwnd == RFB_HWND
        self.opened_on.append(threading.current_thread())
        return self.window

    def _close(self, tab: FakeNode) -> None:
        self.strip.kids.remove(tab)
        self.closed.append(tab.name)

    def _switch(self, tab: FakeNode) -> None:
        for each in self.tabs:
            each.is_active = each is tab
        self.switched.append(tab.name)


def test_the_tabs_past_the_browsers_own_are_closed_last_first():
    chrome = FakeChrome("a", "b", "extension page", "another", active="a")

    close_the_tabs_past_the_browsers_own(chrome.strip, own_tabs=2)

    assert chrome.closed == ["another", "extension page"]
    assert [tab.name for tab in chrome.strip.kids] == ["a", "b"]


def test_a_window_holding_only_its_own_tabs_has_none_closed():
    chrome = FakeChrome("a", "b", active="a")

    close_the_tabs_past_the_browsers_own(chrome.strip, own_tabs=2)

    assert chrome.closed == []


def _an_extra_tab_turns_up_in(chrome: FakeChrome, name: str) -> None:
    tab = FakeNode(name, is_tab=True, on_close=chrome._close)
    chrome.strip.kids = [*chrome.strip.kids, tab]


class _Clock:
    def __init__(self, *readings: float) -> None:
        self._readings = iter(readings)

    def __call__(self) -> float:
        return next(self._readings)


def _closer_for(chrome: FakeChrome, *, clock: _Clock, open_window=None) -> StartupTabCloser:
    return StartupTabCloser(RFB_HWND, own_tabs=2, open_window=open_window or chrome.open_window,
                            watch_s=10, look_every_s=0, clock=clock)


def test_a_tab_that_turns_up_while_the_browser_starts_is_closed():
    chrome = FakeChrome("a", "b", active="a")
    looks: list[int] = []

    def a_clock_that_brings_an_extra_tab_after_the_first_look() -> float:
        looks.append(len(looks))
        if len(looks) == 3:
            _an_extra_tab_turns_up_in(chrome, "extension page")
        return [0, 1, 2, 3, 11][len(looks) - 1]

    StartupTabCloser(RFB_HWND, own_tabs=2, open_window=chrome.open_window, watch_s=10,
                     look_every_s=0, clock=a_clock_that_brings_an_extra_tab_after_the_first_look).watch()

    assert chrome.closed == ["extension page"]


def test_once_the_start_is_over_no_tab_is_looked_at():
    chrome = FakeChrome("a", "b", "a tab he opened", active="a")

    _closer_for(chrome, clock=_Clock(0, 11)).watch()

    assert (chrome.opened_on, chrome.closed) == ([], [])


def test_a_stopped_closer_looks_at_no_tab():
    chrome = FakeChrome("a", "b", "a tab Fun Time opened", active="a")
    closer = _closer_for(chrome, clock=_Clock(0, 1))

    closer.stop()
    closer.watch()

    assert (chrome.opened_on, chrome.closed) == ([], [])


def test_a_browser_that_refuses_while_it_starts_is_asked_again():
    chrome = FakeChrome("a", "b", "extension page", active="a")
    refused: list[int] = []

    def open_window(hwnd: int) -> FakeNode:
        if not refused:
            refused.append(hwnd)
            raise OSError("not answering yet")
        return chrome.open_window(hwnd)

    _closer_for(chrome, clock=_Clock(0, 1, 2, 11), open_window=open_window).watch()

    assert chrome.closed == ["extension page"]


def test_the_closer_watches_on_a_thread_of_its_own_that_stopping_ends():
    chrome = FakeChrome("a", "b", active="a")
    closer = StartupTabCloser(RFB_HWND, own_tabs=2, open_window=chrome.open_window)

    closer.start()
    closer.stop()

    assert "rfb-startup-tabs" not in [thread.name for thread in threading.enumerate()]
    assert threading.current_thread() not in chrome.opened_on


def test_the_next_tab_is_switched_to_on_the_switchers_own_thread():
    chrome = FakeChrome("a", "b", "c", active="a")
    switcher = ChromeTabSwitcher(RFB_HWND, open_window=chrome.open_window)
    try:
        switcher.show_next_tab()
        wait_until(lambda: chrome.switched == ["b"], timeout=10)
    finally:
        switcher.stop()

    assert threading.current_thread() not in chrome.opened_on


def test_the_tab_strip_is_found_once_and_kept():
    chrome = FakeChrome("a", "b", "c", active="a")
    switcher = ChromeTabSwitcher(RFB_HWND, open_window=chrome.open_window)
    try:
        switcher.show_next_tab()
        wait_until(lambda: chrome.switched == ["b"], timeout=10)
        switcher.show_next_tab()
        wait_until(lambda: chrome.switched == ["b", "c"], timeout=10)
    finally:
        switcher.stop()

    assert len(chrome.opened_on) == 1


def test_a_window_whose_tabs_are_not_ready_the_first_time_is_asked_again():
    chrome = FakeChrome("a", "b", active="a")
    ready = chrome.window.kids
    chrome.window.kids = []

    def open_window_that_fills_in_once_asked(hwnd: int) -> FakeNode:
        window = chrome.open_window(hwnd)
        if len(chrome.opened_on) > 1:
            window.kids = ready
        return window

    switcher = ChromeTabSwitcher(RFB_HWND, open_window=open_window_that_fills_in_once_asked,
                                 retry_after_s=0.01)
    try:
        switcher.show_next_tab()
        wait_until(lambda: chrome.switched == ["b"], timeout=10)
    finally:
        switcher.stop()


def test_a_tab_strip_chrome_has_since_replaced_is_found_again():
    chrome = FakeChrome("a", "b", "c", active="a")
    switcher = ChromeTabSwitcher(RFB_HWND, open_window=chrome.open_window, retry_after_s=0.01)
    try:
        switcher.show_next_tab()
        wait_until(lambda: chrome.switched == ["b"], timeout=10)

        def gone() -> list[FakeNode]:
            raise OSError("that element is no longer available")

        chrome.strip.children = gone
        chrome.strip = FakeNode("strip", kids=chrome.tabs)
        chrome.window.kids = [FakeNode("toolbar", kids=[chrome.strip])]

        switcher.show_next_tab()
        wait_until(lambda: chrome.switched == ["b", "c"], timeout=10)
    finally:
        switcher.stop()


def test_a_browser_that_keeps_refusing_is_logged_once_until_it_answers_again(caplog):
    chrome = FakeChrome("a", "b", "c", active="a")
    refusals: list[int] = []
    refusing = True

    def open_window(hwnd: int) -> FakeNode:
        if refusing:
            refusals.append(hwnd)
            raise OSError("not answering")
        return chrome.open_window(hwnd)

    switcher = ChromeTabSwitcher(RFB_HWND, open_window=open_window, retry_after_s=0.001)
    try:
        with caplog.at_level(logging.INFO, logger="fun_time.chrome_tabs"):
            switcher.show_next_tab()
            wait_until(lambda: len(refusals) == TAB_STRIP_ATTEMPTS, timeout=10)
            switcher.show_next_tab()
            wait_until(lambda: len(refusals) == 2 * TAB_STRIP_ATTEMPTS, timeout=10)
            refusing = False
            switcher.show_next_tab()
            wait_until(lambda: chrome.switched == ["b"], timeout=10)
    finally:
        switcher.stop()

    assert [record.levelno for record in caplog.records] == [logging.WARNING, logging.INFO]


def test_a_switcher_nobody_has_asked_yet_runs_no_thread():
    switcher = ChromeTabSwitcher(RFB_HWND, open_window=FakeChrome("a", "b", active="a").open_window)
    try:
        assert "rfb-slideshow" not in [thread.name for thread in threading.enumerate()]
    finally:
        switcher.stop()


def test_stopping_ends_the_switchers_thread_and_says_so(caplog):
    chrome = FakeChrome("a", "b", active="a")
    switcher = ChromeTabSwitcher(RFB_HWND, open_window=chrome.open_window)
    switcher.show_next_tab()
    wait_until(lambda: chrome.switched == ["b"], timeout=10)

    with caplog.at_level(logging.INFO, logger="fun_time.chrome_tabs"):
        switcher.stop()

    assert "RFB slideshow stopped" in caplog.text
    assert "rfb-slideshow" not in [thread.name for thread in threading.enumerate()]


def test_a_window_that_never_shows_its_tabs_is_logged_once(caplog):
    chrome = FakeChrome("a", "b", active="a")
    chrome.window.kids = []
    switcher = ChromeTabSwitcher(RFB_HWND, open_window=chrome.open_window, retry_after_s=0.001)
    try:
        with caplog.at_level(logging.WARNING, logger="fun_time.chrome_tabs"):
            switcher.show_next_tab()
            wait_until(lambda: len(chrome.opened_on) == TAB_STRIP_ATTEMPTS, timeout=10)
            switcher.show_next_tab()
            wait_until(lambda: len(chrome.opened_on) == 2 * TAB_STRIP_ATTEMPTS, timeout=10)
    finally:
        switcher.stop()

    assert [record.levelno for record in caplog.records] == [logging.WARNING]
