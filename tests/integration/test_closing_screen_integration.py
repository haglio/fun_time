"""Real-window checks for the cover a session goes out under.

The unit suite can only fake the tkinter overlay — building the real one puts a
window over every monitor.  Here it is built for real on the hidden desktop,
where it renders to nothing, so the one thing the unit tests cannot reach is
covered: that ``_closing_screen`` actually gets a painted window onto the screen
before it hands teardown the go-ahead, and that the window is gone again by the
time the block ends.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fun_time.closing_screen import STALE_TIMEOUT_S, WINDOW_TITLE
from fun_time.overlay_progress import SHUTDOWN_PROGRESS_FILENAME, ready_file_for
from fun_time.win32 import find_window_by_title
from fun_time.windows_bridge_orchestrator import _closing_screen
from tests.integration.integration_support import let_seconds_pass, this_checkouts_siblings

pytestmark = [
    # The cover is a real window on a real desktop, and the wait for it is the
    # thing under test; the unit conftest's zeroed startup waits do not apply.
    pytest.mark.real_startup_waits,
    pytest.mark.skipif(sys.platform != "win32", reason="launches a real Win32 window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]


def test_the_real_cover_is_on_screen_for_the_whole_teardown(tmp_path: Path):
    """Everything teardown does happens between these two asserts, so the window
    being up at the first and gone by the second is the guarantee the feature is:
    no window of the session is ever seen going out on its own."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    with _closing_screen(state_dir, enabled=True,
                         sibling_checkouts=this_checkouts_siblings()) as progress:
        assert find_window_by_title(WINDOW_TITLE, exact=True), (
            "the cover was not on screen when teardown was let go"
        )
        # The flag teardown waited on came from the screen itself, not from us.
        assert ready_file_for(state_dir / SHUTDOWN_PROGRESS_FILENAME).exists()
        progress.advance("funestras")

    assert not find_window_by_title(WINDOW_TITLE, exact=True), (
        "the cover outlived the teardown it was hiding"
    )
    assert not (state_dir / SHUTDOWN_PROGRESS_FILENAME).exists()


def test_the_real_cover_stays_up_while_a_part_takes_longer_than_its_guard_to_end(
        tmp_path: Path):
    """His quit on a busy machine: the parts took longer to end than the cover
    waits without news, and it went with the dashboard still on screen."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    with _closing_screen(state_dir, enabled=True,
                         sibling_checkouts=this_checkouts_siblings()) as progress:
        progress.advance("funestras")
        with progress.cover_kept_up():
            let_seconds_pass(STALE_TIMEOUT_S + 2)
        still_up = bool(find_window_by_title(WINDOW_TITLE, exact=True))

    assert still_up, "the cover went while a part it was hiding was still ending"
