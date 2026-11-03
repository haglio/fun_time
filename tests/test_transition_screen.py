from __future__ import annotations

import inspect
import os
import tempfile
import threading
import time
from pathlib import Path

from fun_time import orchestrator as desktop_orchestrator
from fun_time import session_handoff, transition_screen
from fun_time.child_launch import open_child_log
from fun_time.overlay_progress import LOADING_SCREEN_HEARTBEAT_S
from fun_time.session_handoff import (
    LOADING_SCREEN_STALE_S,
    crossing_progress_path,
    drop_transition_screen,
    keep_the_transition_screen,
    returning_from_a_crossing,
)
from fun_time_vr import orchestrator as vr_orchestrator


def test_it_gives_up_in_a_time_a_person_will_wait():
    assert transition_screen.STALE_TIMEOUT_S <= 30.0


def test_a_slow_crossing_keeps_its_own_loading_screen_up():
    assert LOADING_SCREEN_HEARTBEAT_S * 3 < transition_screen.STALE_TIMEOUT_S

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


def test_a_crossing_has_no_process_that_forgets_to_say_so():
    for func in (
        desktop_orchestrator.main,
        vr_orchestrator.main,
        session_handoff.run,
    ):
        assert "keep_the_transition_screen(" in inspect.getsource(func), func.__qualname__


def test_a_crossing_file_nobody_tidied_is_not_a_crossing():
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


def test_a_child_log_that_cannot_be_opened_does_not_stop_the_child():
    handle = open_child_log(Path("Z:/no/such/place/relay.log"), ["a", "b"])
    try:
        handle.write(b"still runs\n")
    finally:
        handle.close()
