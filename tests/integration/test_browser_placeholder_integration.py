from __future__ import annotations

import os
import sys

import pytest

from fun_time.browser_placeholder import TITLE, stand_unseen_over
from fun_time.win32 import find_window_by_title, is_window_topmost, restore_window, window_rect
from fun_time.window_layout import WindowRect
from tests.integration.integration_support import ended_within, seen_within, this_checkouts_siblings

pytestmark = [
    pytest.mark.real_startup_waits,
    pytest.mark.skipif(sys.platform != "win32", reason="launches a real Win32 window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

RECT = WindowRect(x=40, y=30, width=320, height=240)


def test_the_placeholder_stands_unseen_over_the_browsers_place_until_shown_and_then_ends():
    placeholder = stand_unseen_over(RECT, sibling_checkouts=this_checkouts_siblings())
    try:
        assert placeholder.hwnd, "the placeholder's window never came up"
        assert not find_window_by_title(TITLE, exact=True), "it showed before it was told to"
        assert is_window_topmost(placeholder.hwnd)

        restore_window(placeholder.hwnd, activate=False)

        assert seen_within(lambda: find_window_by_title(TITLE, exact=True), seconds=5.0)
        assert window_rect(placeholder.hwnd) == tuple(RECT)
    finally:
        placeholder.take_it_down()

    assert ended_within(placeholder.process, 10.0)
