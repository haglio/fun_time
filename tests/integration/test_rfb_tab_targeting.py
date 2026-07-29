"""A lock's tab must land in Fun Time's own Chrome window, not the user's.

The Random Favs Browser is a window of the user's *own* Chrome — his user data
directory, his profile — so windows of that profile he already had open are
candidates for a lock's tab too, and nothing about handing Chrome a URL says
which window is meant.  Chrome forwards a second chrome.exe's command line to
the running browser (the singleton is keyed on the user data directory), which
resolves the profile from ``--profile-directory`` and asks ``FindTabbedBrowser``
for a window: it walks its browsers most-recently-active first and takes the
first one whose profile matches.  So a personal window he touched a moment ago
beats the RFB, and the tab lands there — behind the players, unseen until later.

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
import time
from pathlib import Path

import pytest

from fun_time.win32 import close_window, find_window_by_title, force_foreground_window
from fun_time.windows_bridge_random_favs_browser import open_rfb_tab

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="drives a real Chrome through Win32"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

_CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)

# Fabricated markers, so a window is identified by a title no real page carries.
RFB_MARKER = "FUNTIMEMARK-RFB"
PERSONAL_MARKER = "FUNTIMEMARK-PERSONAL"
CONTROL_MARKER = "FUNTIMEMARK-CONTROL"
LOCKED_MARKER = "FUNTIMEMARK-LOCKED"

# A cold Chrome on a brand-new user data directory is the slow one; every launch
# after it only has to reach the running browser.
_FIRST_WINDOW_TIMEOUT_S = 60.0
_TAB_TIMEOUT_S = 30.0


def _chrome_exe() -> Path:
    for candidate in _CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    pytest.skip("Chrome is not installed at either standard location")


def _page(directory: Path, marker: str) -> str:
    path = directory / f"{marker}.html"
    path.write_text(f"<!doctype html><title>{marker}</title><h1>{marker}</h1>", encoding="utf-8")
    return path.as_uri()


def _await_window(marker: str, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_window_by_title(marker)
        if hwnd:
            return hwnd
        time.sleep(0.2)
    return 0


def test_a_locked_videos_tab_goes_to_fun_times_window_not_the_users(tmp_path: Path):
    chrome = _chrome_exe()
    pages = tmp_path / "pages"
    pages.mkdir()
    # Every launch shares this directory, so they share one Chrome and one
    # profile — the very thing that makes the two windows interchangeable to
    # Chrome, and what the test needs in order to mean anything.  It is a fresh
    # directory under the run's tmp_path, so the user's own Chrome is untouched.
    args = " ".join([
        f'--user-data-dir="{tmp_path / "chrome_user_data"}"',
        "--no-first-run",
        "--no-default-browser-check",
    ])

    def launch(marker: str, *, new_window: bool) -> None:
        """Hand Chrome a page exactly the way a lock does — through production's
        own launcher, so a change to how the tab handoff is built is a change
        this test sees."""
        open_rfb_tab(
            urls=[_page(pages, marker)],
            shortcut_target=str(chrome),
            shortcut_work_dir=str(chrome.parent),
            shortcut_args=f"{args} --new-window" if new_window else args,
        )

    launch(RFB_MARKER, new_window=True)
    rfb_hwnd = _await_window(RFB_MARKER, _FIRST_WINDOW_TIMEOUT_S)
    assert rfb_hwnd, "Fun Time's Chrome window never appeared"

    launch(PERSONAL_MARKER, new_window=True)
    personal_hwnd = _await_window(PERSONAL_MARKER, _TAB_TIMEOUT_S)
    assert personal_hwnd, "the stand-in for the user's own Chrome window never appeared"
    assert personal_hwnd != rfb_hwnd

    try:
        # The control.  The user's window opened last, so it is the most recently
        # activated one of the profile, and a plain handoff goes there: this is
        # the bug, reproduced.  Without it a build whose activation did nothing
        # would still pass the half below.
        launch(CONTROL_MARKER, new_window=False)
        assert _await_window(CONTROL_MARKER, _TAB_TIMEOUT_S) == personal_hwnd, (
            "a plain handoff was expected to land in the most recently activated "
            "window of the profile"
        )

        # And the fix: activating Fun Time's window puts it at the head of
        # Chrome's activation order, so it is the one FindTabbedBrowser returns.
        # The return value is not asserted on — a non-input desktop has no
        # foreground window to become, so it reads False here while the
        # activation itself still lands, which is what the tab proves.
        force_foreground_window(rfb_hwnd)
        launch(LOCKED_MARKER, new_window=False)
        assert _await_window(LOCKED_MARKER, _TAB_TIMEOUT_S) == rfb_hwnd, (
            "the locked video's tab landed somewhere other than Fun Time's window"
        )
    finally:
        # WM_CLOSE on both windows is the whole teardown: closing the last window
        # of a Chrome exits it.  Nothing sweeps by name here — the run's job
        # object is what guarantees no leftovers, and a name-matched kill is
        # machine-wide, which is how a sweep once reached the user's own players.
        for hwnd in (rfb_hwnd, personal_hwnd):
            close_window(hwnd)
        # Chrome writes its profile out on the way down; give it that beat before
        # the tmp_path teardown pulls the directory out from under it.
        time.sleep(2)
