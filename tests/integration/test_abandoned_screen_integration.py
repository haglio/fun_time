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

from fun_time import loading_screen, transition_screen
from fun_time.session_handoff import VR, crossing_progress_path, launch_crossing_cover
from fun_time.win32 import find_window_for_process
from fun_time.win32_process import is_process_alive
from tests.child_reports import all_gone_within, pid_written_to, with_what_it_started
from tests.integration.integration_support import RELEASE_BUDGET_S, checkout_project_dirs
from tests.scratch import remove_scratch

pytestmark = [
    pytest.mark.real_startup_waits,
    pytest.mark.skipif(sys.platform != "win32", reason="launches a real Win32 window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

ROOT = Path(__file__).resolve().parents[2]
A_STARVED_SCREENS_SLACK_S = 90.0

_RAISES_A_LOADING_SCREEN = """
import pathlib, time
from fun_time.loading_cover import open_the_cover
cover = open_the_cover(pathlib.Path({state!r}), show_overlays=True, project_dirs={dirs!r})
pathlib.Path({report!r}).write_text(str(cover.process.pid))
time.sleep(120)
"""


def _its_window_came_up(pid: int, title: str, *, within_s: float) -> bool:
    """By the screen's own process: an earlier test's screen wears the same
    title, and may still be on this desktop."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline and is_process_alive(pid):
        if find_window_for_process(pid, title):
            return True
        time.sleep(0.1)
    return False


def _removed_once_released(folder: Path) -> None:
    """A process ended from outside reads as dead a moment before Windows has
    closed the files it had open."""
    deadline = time.monotonic() + RELEASE_BUDGET_S
    while folder.exists():
        try:
            remove_scratch(folder)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


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
        assert _its_window_came_up(screen.pid, transition_screen.WINDOW_TITLE, within_s=60.0), (
            "the screen never came up")

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


def test_a_loading_screen_goes_the_moment_the_fun_time_that_raised_it_dies(tmp_path: Path):
    """His answer when asked whether a crashed Fun Time's loading screen
    should sit out its minute first: no, it goes at once."""
    report = tmp_path / "screen_pid.txt"
    fun_time = subprocess.Popen(
        [sys.executable, "-c", _RAISES_A_LOADING_SCREEN.format(
            state=str(tmp_path / "state"), dirs=checkout_project_dirs(), report=str(report))],
        cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        screen = pid_written_to(report, by=fun_time)
        assert _its_window_came_up(screen, loading_screen.WINDOW_TITLE, within_s=60.0), (
            "the loading screen never came up")
        screen_and_its_own = with_what_it_started(screen)

        fun_time.kill()
        fun_time.wait()

        assert all_gone_within(screen_and_its_own, 5.0), (
            "the loading screen outlived the Fun Time that raised it")
    finally:
        if fun_time.poll() is None:
            fun_time.kill()
            fun_time.wait()
        _removed_once_released(tmp_path / "state")
