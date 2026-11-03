from __future__ import annotations

import os
import sys

import pytest
from app_support.win32 import force_foreground_window

from fun_time.chrome_accessibility import open_chrome_window
from fun_time.chrome_tabs import ChromeTabSwitcher
from tests.integration.integration_support import await_window
from tests.integration.throwaway_chrome import TAB_TIMEOUT_S

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="drives a real Chrome through Win32"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

FIRST_SLIDE = "FUNTIMEMARK-SLIDE-FIRST"
SECOND_SLIDE = "FUNTIMEMARK-SLIDE-SECOND"
THIRD_SLIDE = "FUNTIMEMARK-SLIDE-THIRD"
HIS_OWN_PAGE = "FUNTIMEMARK-HIS-OWN-PAGE"
AFTER_THE_SLIDE = "FUNTIMEMARK-AFTER-THE-SLIDE"
AFTER_AN_ACTIVATION = "FUNTIMEMARK-AFTER-AN-ACTIVATION"

# Chrome builds its accessibility tree when it is first asked about it, and under
# the flake gate's load the first walk of a new window has taken 72 seconds.
FIRST_SLIDE_TIMEOUT_S = 120.0


@pytest.fixture
def switcher_for():
    switchers: list[ChromeTabSwitcher] = []

    def start(hwnd: int) -> ChromeTabSwitcher:
        switchers.append(ChromeTabSwitcher(hwnd, open_window=open_chrome_window))
        return switchers[-1]

    yield start
    for switcher in switchers:
        switcher.stop()


def test_the_slideshow_shows_each_tab_in_turn_and_starts_over_after_the_last(chrome, switcher_for):
    rfb = chrome.open_window(FIRST_SLIDE, SECOND_SLIDE, THIRD_SLIDE)
    tabs = switcher_for(rfb)

    tabs.show_next_tab()
    assert await_window(SECOND_SLIDE, FIRST_SLIDE_TIMEOUT_S) == rfb
    for showing_next in (THIRD_SLIDE, FIRST_SLIDE):
        tabs.show_next_tab()
        assert await_window(showing_next, TAB_TIMEOUT_S) == rfb


def test_a_slide_leaves_his_own_window_the_one_chrome_hands_his_links_to(chrome, switcher_for):
    rfb = chrome.open_window(FIRST_SLIDE, SECOND_SLIDE)
    his_window = chrome.open_window(HIS_OWN_PAGE)

    switcher_for(rfb).show_next_tab()
    assert await_window(SECOND_SLIDE, FIRST_SLIDE_TIMEOUT_S) == rfb

    assert chrome.hand_over(AFTER_THE_SLIDE) == his_window
    force_foreground_window(rfb)
    assert chrome.hand_over(AFTER_AN_ACTIVATION) == rfb
