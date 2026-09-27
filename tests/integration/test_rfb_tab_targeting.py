"""A lock's tab must land in Fun Time's own Chrome window, not the user's.

The Random Favs Browser is a window of the user's *own* Chrome — his user data
directory, his profile — so windows of that profile he already had open are
candidates for a lock's tab too, and nothing about handing Chrome a URL says
which window is meant.  Chrome forwards a second chrome.exe's command line to
the running browser (the singleton is keyed on the user data directory), which
resolves the profile from ``--profile-directory`` and asks ``FindTabbedBrowser``
for a window: it walks its browsers most-recently-active first and takes the
first one whose profile matches.  So a personal window he touched a moment ago
beats the RFB, and the tab lands there — under the players, unseen until later.

Only a real Chrome can show that, which is why this is an integration test: it
opens two windows of one profile in a **throwaway user data directory** (nothing
of the user's is touched, and no session is involved), lets the "personal" one be
the most recently activated, and then checks both halves — that the tab really
does go there when nothing intervenes, and that
``force_foreground_window`` on Fun Time's window is enough to take it back.
Without the control half the test would pass on a build where the activation did
nothing at all.
"""
from __future__ import annotations

import os
import sys

import pytest

from fun_time.win32 import force_foreground_window
from fun_time.windows_bridge_random_favs_browser import open_rfb_tab
from tests.integration.throwaway_chrome import TAB_TIMEOUT_S, await_window

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="drives a real Chrome through Win32"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

# Fabricated markers, so a window is identified by a title no real page carries.
RFB_MARKER = "FUNTIMEMARK-RFB"
PERSONAL_MARKER = "FUNTIMEMARK-PERSONAL"
CONTROL_MARKER = "FUNTIMEMARK-CONTROL"
LOCKED_MARKER = "FUNTIMEMARK-LOCKED"


def test_a_locked_videos_tab_goes_to_fun_times_window_not_the_users(chrome):
    def hand_over_like_a_lock(marker: str) -> int:
        """Hand Chrome a page exactly the way a lock does — through production's
        own launcher, so a change to how the tab handoff is built is a change
        this test sees."""
        open_rfb_tab(urls=[chrome.page_for(marker)], shortcut=chrome.shortcut())
        return await_window(marker, TAB_TIMEOUT_S)

    rfb_hwnd = chrome.open_window(RFB_MARKER)
    assert rfb_hwnd, "Fun Time's Chrome window never appeared"
    personal_hwnd = chrome.open_window(PERSONAL_MARKER)
    assert personal_hwnd, "the stand-in for the user's own Chrome window never appeared"
    assert personal_hwnd != rfb_hwnd

    # The control.  The user's window opened last, so it is the most recently
    # activated one of the profile, and a plain handoff goes there: this is
    # the bug, reproduced.  Without it a build whose activation did nothing
    # would still pass the half below.
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
