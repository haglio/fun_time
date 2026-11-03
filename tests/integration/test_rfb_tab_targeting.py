from __future__ import annotations

import os
import sys

import pytest
from app_support.win32 import force_foreground_window

from fun_time.windows_bridge_random_favs_browser import open_rfb_tab
from tests.integration.integration_support import await_window
from tests.integration.throwaway_chrome import TAB_TIMEOUT_S

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="drives a real Chrome through Win32"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

RFB_MARKER = "FUNTIMEMARK-RFB"
PERSONAL_MARKER = "FUNTIMEMARK-PERSONAL"
CONTROL_MARKER = "FUNTIMEMARK-CONTROL"
LOCKED_MARKER = "FUNTIMEMARK-LOCKED"


def test_a_locked_videos_tab_goes_to_fun_times_window_not_the_users(chrome):
    def hand_over_like_a_lock(marker: str) -> int:
        open_rfb_tab(urls=[chrome.page_for(marker)], shortcut=chrome.shortcut())
        return await_window(marker, TAB_TIMEOUT_S)

    rfb_hwnd = chrome.open_window(RFB_MARKER)
    assert rfb_hwnd, "Fun Time's Chrome window never appeared"
    personal_hwnd = chrome.open_window(PERSONAL_MARKER)
    assert personal_hwnd, "the stand-in for the user's own Chrome window never appeared"
    assert personal_hwnd != rfb_hwnd

    assert hand_over_like_a_lock(CONTROL_MARKER) == personal_hwnd, (
        "a plain handoff was expected to land in the most recently activated "
        "window of the profile"
    )

    # And the fix: activating Fun Time's window puts it at the head of
    # Chrome's activation order, so it is the one FindTabbedBrowser returns.
    # The return value is not asserted on — a non-input desktop has no
    # foreground window to become, so it reads False here while the
    # activation itself still lands, which is what the tab proves.
    force_foreground_window(rfb_hwnd)
    assert hand_over_like_a_lock(LOCKED_MARKER) == rfb_hwnd, (
        "the locked video's tab landed somewhere other than Fun Time's window"
    )
