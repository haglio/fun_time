from __future__ import annotations

import inspect
import os
import tempfile
import threading
import time

from fun_time import orchestrator as desktop_orchestrator
from fun_time import session_handoff, transition_screen
from fun_time.overlay_progress import LOADING_SCREEN_HEARTBEAT_S
from fun_time.session_handoff import (
    LOADING_SCREEN_STALE_S,
    crossing_progress_path,
    drop_transition_screen,
    keep_the_transition_screen,
    returning_from_a_crossing,
)
from fun_time_vr import orchestrator as vr_orchestrator

LONGEST_COVER_NOT_TAKEN_FOR_A_DEAD_MACHINE_S = 30.0
BEATS_A_BUSY_SESSION_MAY_MISS = 3


def test_it_gives_up_before_its_cover_is_taken_for_a_dead_machine():
    assert transition_screen.STALE_TIMEOUT_S <= LONGEST_COVER_NOT_TAKEN_FOR_A_DEAD_MACHINE_S


def test_a_session_that_misses_some_beats_keeps_its_loading_screen_up():
    assert (LOADING_SCREEN_HEARTBEAT_S * BEATS_A_BUSY_SESSION_MAY_MISS
            < transition_screen.STALE_TIMEOUT_S)


def test_a_slow_crossing_keeps_its_own_loading_screen_up():
    with tempfile.TemporaryDirectory() as state_dir:
        progress = crossing_progress_path(state_dir)
        progress.write_text("1/2|Changing over...", encoding="utf-8")
        os.utime(progress, (0, 0))

        stop = keep_the_transition_screen(state_dir)
        try:
            deadline = time.time() + 5
            while progress.stat().st_mtime < time.time() - 60 and time.time() < deadline:
                time.sleep(0.05)
        finally:
            stop()

        assert progress.stat().st_mtime > time.time() - 60
        assert progress.read_text(encoding="utf-8") == "1/2|Changing over..."


def test_a_heartbeat_that_is_stopped_is_gone():
    before = set(threading.enumerate())
    with tempfile.TemporaryDirectory() as state_dir:
        stop = keep_the_transition_screen(state_dir)

        stop()

    assert set(threading.enumerate()) <= before


def test_every_process_a_crossing_passes_through_keeps_its_screen_up():
    for func in (
        desktop_orchestrator.main,
        vr_orchestrator.main,
        session_handoff.run,
    ):
        assert "keep_the_transition_screen(" in inspect.getsource(func), func.__qualname__


def test_a_crossing_file_older_than_its_screen_would_stand_is_not_a_return():
    with tempfile.TemporaryDirectory() as state_dir:
        progress = crossing_progress_path(state_dir)
        progress.write_text("1/2|Cancelling...", encoding="utf-8")
        assert returning_from_a_crossing(state_dir)

        os.utime(progress, (time.time() - LOADING_SCREEN_STALE_S - 1,) * 2)
        assert not returning_from_a_crossing(state_dir)

        drop_transition_screen(state_dir)
        stop = keep_the_transition_screen(state_dir)
        try:
            time.sleep(0.3)
            os.utime(progress, (time.time() - LOADING_SCREEN_STALE_S - 1,) * 2)
            time.sleep(0.3)
        finally:
            stop()
        assert not returning_from_a_crossing(state_dir)
