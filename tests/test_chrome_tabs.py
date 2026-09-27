from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from app_support.threading_utils import wait_until

from fun_time.chrome_tabs import (
    TAB_STRIP_ATTEMPTS,
    ChromeTabSwitcher,
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

    def children(self) -> list[FakeNode]:
        self.walked_into = True
        return list(self.kids)

    def switch_to(self) -> None:
        assert self.on_switch is not None
        self.on_switch(self)


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
        self.strip = FakeNode("strip", kids=self.tabs)
        self.window = FakeNode("window", kids=[FakeNode("toolbar", kids=[self.strip])])
        self.opened_on: list[threading.Thread] = []
        self.switched: list[str] = []

    def open_window(self, hwnd: int) -> FakeNode:
        assert hwnd == RFB_HWND
        self.opened_on.append(threading.current_thread())
        return self.window

    def _switch(self, tab: FakeNode) -> None:
        for each in self.tabs:
            each.is_active = each is tab
        self.switched.append(tab.name)


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
