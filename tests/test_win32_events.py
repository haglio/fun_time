from __future__ import annotations

import threading

from fun_time.win32_events import PressAndCaretWatch


def test_no_unit_test_listens_to_the_presses_on_his_desktop():
    watch = PressAndCaretWatch()

    watch.start()

    assert "press-and-caret-watch" not in {thread.name for thread in threading.enumerate()}
    assert watch.take() == []


def test_a_watch_that_never_started_stops_without_complaint():
    PressAndCaretWatch().stop()
