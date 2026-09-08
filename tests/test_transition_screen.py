"""The crossing cover's two ways out that need nobody else alive.

It cost him two forced restarts of the machine.  A crossing raises a
full-screen always-on-top window and the ARRIVING session takes it down -- so
every way that session can fail to arrive is a way the monitors stay covered
with no keyboard route out of it, and there are more of those than can be
enumerated: the relay died on a locked log file once, and something else will
die on something else.

So the cover stops depending on anyone: Esc takes it down itself, and a file
that has stopped moving takes it down without being asked.
"""
from __future__ import annotations

import inspect
import os
import tempfile
import time
from pathlib import Path

from fun_time import transition_screen
from fun_time.overlay_window import OverlayWindow


def test_it_gives_up_in_a_time_a_person_will_wait():
    """Three minutes under a cover is indistinguishable from a dead machine --
    he force-restarted rather than sit it out.  A crossing takes about two
    seconds, and a slow one keeps saying so."""
    assert transition_screen.STALE_TIMEOUT_S <= 30.0


def test_it_is_raised_dismissable_and_says_so():
    """The half no timeout covers: a person who wants it gone now."""
    source = inspect.getsource(transition_screen.main)

    assert "dismissable=True" in source
    assert "hint=DISMISS_HINT" in source
    assert "Esc" in transition_screen.DISMISS_HINT


def test_a_dismissable_cover_binds_escape_to_its_own_destruction():
    """Not to a request that something else act on -- that is the dependency
    this exists to remove.  The window it belongs to is the thing that goes."""
    source = inspect.getsource(OverlayWindow.__init__)
    branch = source[source.index("elif dismissable:"):]

    assert "self._root.bind(\"<Escape>\"" in branch
    assert "destroy()" in branch.split("focus_force")[0]


def test_the_cancel_affordance_still_wins_where_there_is_one():
    """A launch cover's Esc asks the orchestrator to unwind, which is a
    different thing from closing a window, and it is bound first."""
    source = inspect.getsource(OverlayWindow.__init__)

    assert source.index("if cancel is not None:") < source.index("elif dismissable:")


def test_a_slow_crossing_keeps_its_own_cover_up():
    """The other half of a twenty-second timeout: a headset that takes half a
    minute to wake must not be uncovered halfway there.  So a session says the
    crossing is still under way -- without rewriting the line under it, which a
    torn read would blank on the screen."""
    from fun_time.session_handoff import (
        COVER_HEARTBEAT_S,
        crossing_progress_path,
        keep_the_crossing_cover,
    )

    assert COVER_HEARTBEAT_S * 3 < transition_screen.STALE_TIMEOUT_S  # room to miss some

    with tempfile.TemporaryDirectory() as state_dir:
        progress = crossing_progress_path(state_dir)
        progress.write_text("1/2|Changing over...", encoding="utf-8")
        os.utime(progress, (0, 0))

        keep_the_crossing_cover(state_dir)
        deadline = time.time() + 5
        while progress.stat().st_mtime < time.time() - 60 and time.time() < deadline:
            time.sleep(0.05)

        assert progress.stat().st_mtime > time.time() - 60
        assert progress.read_text(encoding="utf-8") == "1/2|Changing over..."


def test_a_crossing_has_no_process_that_forgets_to_say_so():
    """A crossing passes through three processes -- the session being left, the
    relay, the session arriving -- and the cover stands across all three.  Any
    one of them that does not say so uncovers the monitors mid-crossing, which
    is worse than the trap this timeout was shortened to escape."""
    from fun_time import orchestrator as desktop_orchestrator
    from fun_time import session_handoff
    from fun_time_vr import orchestrator as vr_orchestrator

    for func in (
        desktop_orchestrator.main,      # Fun Time, leaving or arriving
        vr_orchestrator.main,           # FunTimeVR, leaving or arriving
        session_handoff.run,            # the relay in between
    ):
        assert "keep_the_crossing_cover(" in inspect.getsource(func), func.__qualname__


def test_a_crossing_file_nobody_tidied_is_not_a_crossing():
    """His state dir still holds ``1/2|Cancelling...`` from the run that
    stranded him.  Only the cover deletes that file, so a crossing that ended
    without one leaks it -- and a startup that reads it as a return builds an
    UNCANCELLABLE loading screen, for that session and every session after."""
    from fun_time.session_handoff import (
        COVER_STALE_S,
        crossing_progress_path,
        drop_crossing_cover,
        keep_the_crossing_cover,
        returning_from_a_crossing,
    )

    with tempfile.TemporaryDirectory() as state_dir:
        progress = crossing_progress_path(state_dir)
        progress.write_text("1/2|Cancelling...", encoding="utf-8")
        assert returning_from_a_crossing(state_dir)  # a cover IS standing

        os.utime(progress, (time.time() - COVER_STALE_S - 1,) * 2)
        assert not returning_from_a_crossing(state_dir)

        # Nor does the session that handed over keep the leak looking alive.
        drop_crossing_cover(state_dir)
        keep_the_crossing_cover(state_dir)
        time.sleep(0.3)
        os.utime(progress, (time.time() - COVER_STALE_S - 1,) * 2)
        time.sleep(0.3)
        assert not returning_from_a_crossing(state_dir)


def test_a_child_log_that_cannot_be_opened_does_not_stop_the_child():
    """The specific way it stranded him: the relay bringing Fun Time back opened
    its log first, the file was locked, and the process died before it had done
    anything at all."""
    from fun_time.child_log import open_child_log

    handle = open_child_log(Path("Z:/no/such/place/relay.log"), ["a", "b"])
    try:
        handle.write(b"still runs\n")
    finally:
        handle.close()
