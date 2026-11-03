from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fun_time.closing_screen import STALE_TIMEOUT_S, WINDOW_TITLE
from fun_time.overlay_progress import SHUTDOWN_PROGRESS_FILENAME, ready_file_for
from fun_time.win32 import find_window_by_title
from fun_time.windows_bridge_orchestrator import _closing_screen
from tests.integration import run_clock
from tests.integration.integration_support import let_seconds_pass, this_checkouts_siblings

pytestmark = [
    pytest.mark.real_startup_waits,
    pytest.mark.skipif(sys.platform != "win32", reason="launches a real Win32 window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]


def test_the_real_loading_screen_is_on_screen_for_the_whole_teardown(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    with _closing_screen(state_dir, enabled=True, sibling_checkouts=this_checkouts_siblings(),
                         now=run_clock.now) as progress:
        assert find_window_by_title(WINDOW_TITLE, exact=True), (
            "the loading screen was not on screen when teardown was let go"
        )
        assert ready_file_for(state_dir / SHUTDOWN_PROGRESS_FILENAME).exists()
        progress.advance("funestras")

    assert not find_window_by_title(WINDOW_TITLE, exact=True), (
        "the loading screen outlived the teardown it was hiding"
    )
    assert not (state_dir / SHUTDOWN_PROGRESS_FILENAME).exists()


def test_the_real_loading_screen_stays_up_while_a_part_takes_longer_than_its_guard_to_end(
        tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    with _closing_screen(state_dir, enabled=True, sibling_checkouts=this_checkouts_siblings(),
                         now=run_clock.now) as progress:
        progress.advance("funestras")
        with progress.loading_screen_kept_up():
            let_seconds_pass(STALE_TIMEOUT_S + 2)
        still_up = bool(find_window_by_title(WINDOW_TITLE, exact=True))

    assert still_up, "the loading screen went while a part it was hiding was still ending"
