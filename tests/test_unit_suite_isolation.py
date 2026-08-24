"""What a unit run may not spend, and what it may not touch.

The conftest fixtures pinned here are invisible when they break: the suite stays
green either way, only slower or dirtier.  So each of them gets a test.
"""
from __future__ import annotations

import time

import pytest

from fun_time import win32, windows_bridge_orchestrator

# Read before the first fixture runs, so these are the numbers a session spends.
REAL_STARTUP_TIMEOUTS = {
    "CLOSING_SCREEN_READY_TIMEOUT_S": windows_bridge_orchestrator.CLOSING_SCREEN_READY_TIMEOUT_S,
    "POST_LOADING_RESOLVE_TIMEOUT_S": windows_bridge_orchestrator.POST_LOADING_RESOLVE_TIMEOUT_S,
}


def test_a_unit_test_never_waits_out_a_window_no_test_opened():
    """A unit test mocks the startup that would have opened the session's
    windows, so every lookup the orchestrator makes can only time out — and it
    times out on the wall clock, because the waiter polls with ``time.sleep``.
    One startup pass is the loading cover plus five role resolutions, which was
    20s a test and 359s of ``test_windows_bridge_orchestrator.py`` alone.
    """
    started = time.monotonic()

    hwnd = windows_bridge_orchestrator.wait_for_window_by_title(
        "A window this suite never opens", timeout_s=5.0, exact=True
    )

    assert hwnd == 0
    assert time.monotonic() - started < 1.0


def test_a_unit_test_never_waits_out_a_startup_timeout():
    """The numbers behind those waits, zeroed alongside the lookup itself.

    The hold for the closing cover to report itself painted is timed by the
    orchestrator rather than through the lookup at all; and the per-role budget
    is what the finishing pass hands each lookup, so a test that supplies a
    waiter of its own — a side effect that answers on a later call rather than
    the first — would still spend it in full.
    """
    assert windows_bridge_orchestrator.CLOSING_SCREEN_READY_TIMEOUT_S == 0
    assert windows_bridge_orchestrator.POST_LOADING_RESOLVE_TIMEOUT_S == 0


@pytest.mark.real_startup_waits
def test_the_waits_are_stubbed_per_test_and_not_edited_out_of_the_module():
    """Both of those numbers are pinned elsewhere as production numbers — the
    resolve budget against the cover's staleness guard, the closing-screen hold
    by the tests that walk its three ways out.  Stub them for the session
    instead of per test and those pins read off zeros and stop pinning
    anything, which is a green suite saying nothing.
    """
    for name, real in REAL_STARTUP_TIMEOUTS.items():
        assert getattr(windows_bridge_orchestrator, name) == real
        assert real > 0
    assert windows_bridge_orchestrator.wait_for_window_by_title is (
        win32.wait_for_window_by_title
    )
