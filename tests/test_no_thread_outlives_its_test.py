from __future__ import annotations

import threading
import time

import pytest

from tests.thread_state import ThreadsLeftRunning, no_thread_outlives_this


def _waiting_on(release: threading.Event, name: str, *, then_s: float = 0.0) -> threading.Thread:
    thread = threading.Thread(target=lambda: (release.wait(), time.sleep(then_s)), name=name,
                              daemon=True)
    thread.start()
    return thread


def test_a_thread_started_inside_and_never_told_to_end_is_named():
    release = threading.Event()
    try:
        with pytest.raises(ThreadsLeftRunning, match="never-told-to-end"):
            with no_thread_outlives_this(within_s=0.05):
                left = _waiting_on(release, "never-told-to-end")
    finally:
        release.set()
        left.join()


def test_a_thread_told_to_end_is_waited_for_until_it_has():
    release = threading.Event()

    with no_thread_outlives_this():
        ending = _waiting_on(release, "told-to-end", then_s=0.2)
        release.set()

    assert not ending.is_alive()


def test_a_thread_already_running_before_it_began_is_left_alone():
    release = threading.Event()
    earlier = _waiting_on(release, "already-running")
    try:
        with no_thread_outlives_this(within_s=0.05):
            pass
    finally:
        release.set()
        earlier.join()


def test_every_test_runs_inside_it(request):
    assert "_no_thread_outlives_its_test" in request.fixturenames
