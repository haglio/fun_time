"""What a unit run may not spend, and what it may not touch.

The conftest fixtures pinned here are invisible when they break: the suite stays
green either way, only slower or dirtier.  So each of them gets a test.
"""
from __future__ import annotations

import time

from fun_time import windows_bridge_orchestrator


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
