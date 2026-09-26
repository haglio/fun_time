"""A real screen over every monitor, left with nothing to take it down.

The unit suite drives the screens' polls over fake Tk widgets; here the real
process runs on the hidden desktop, so what is checked is the thing he saw:
whether the window goes away on its own.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fun_time import transition_screen
from fun_time.session_handoff import VR, crossing_progress_path, launch_crossing_cover
from fun_time.win32 import find_window_for_process
from tests.integration.integration_support import checkout_project_dirs

pytestmark = [
    pytest.mark.real_startup_waits,
    pytest.mark.skipif(sys.platform != "win32", reason="launches a real Win32 window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

A_STARVED_SCREENS_SLACK_S = 90.0


def _its_window_came_up(screen: subprocess.Popen, *, within_s: float) -> bool:
    """By the screen's own process: an earlier test's crossing screen wears
    the same title, and may still be on this desktop."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline and screen.poll() is None:
        if find_window_for_process(screen.pid, transition_screen.WINDOW_TITLE):
            return True
        time.sleep(0.1)
    return False


def test_a_screen_whose_progress_file_goes_before_its_first_look_takes_itself_down(
        tmp_path: Path):
    """What forced his two restarts on 2026-09-26: a unit test's loading
    screen lost its progress file with the test's scratch folder before its
    first look, and stood over every monitor for good.  The VR/Desktop
    screen here, for its twenty-second wait; the loading screen runs the same
    poll on a minute."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    screen = launch_crossing_cover(state_dir, VR, project_dirs=checkout_project_dirs())
    crossing_progress_path(state_dir).unlink()
    try:
        assert _its_window_came_up(screen, within_s=60.0), "the screen never came up"

        budget_s = transition_screen.STALE_TIMEOUT_S + A_STARVED_SCREENS_SLACK_S
        try:
            ended_with = screen.wait(timeout=budget_s)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the screen was still up {budget_s:.0f}s after it came up, "
                        f"on a {transition_screen.STALE_TIMEOUT_S:.0f}s wait")

        assert ended_with == 0, "it went, but by dying rather than by giving up"
    finally:
        if screen.poll() is None:
            screen.kill()
            screen.wait()
