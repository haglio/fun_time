from __future__ import annotations

import subprocess
import sys
import time

import pytest

from fun_time import (
    closing_screen,
    loading_screen,
    transition_screen,
    win32,
    windows_bridge_orchestrator,
)

REAL_STARTUP_TIMEOUTS = {
    "CLOSING_SCREEN_READY_TIMEOUT_S": windows_bridge_orchestrator.CLOSING_SCREEN_READY_TIMEOUT_S,
    "POST_LOADING_RESOLVE_TIMEOUT_S": windows_bridge_orchestrator.POST_LOADING_RESOLVE_TIMEOUT_S,
}


def test_a_unit_test_never_waits_out_a_window_no_test_opened():
    started = time.monotonic()

    hwnd = windows_bridge_orchestrator.wait_for_window_by_title(
        "A window this suite never opens", timeout_s=5.0, exact=True
    )

    assert hwnd == 0
    assert time.monotonic() - started < 1.0


def test_a_unit_test_never_waits_out_a_startup_timeout():
    assert windows_bridge_orchestrator.CLOSING_SCREEN_READY_TIMEOUT_S == 0
    assert windows_bridge_orchestrator.POST_LOADING_RESOLVE_TIMEOUT_S == 0


@pytest.mark.real_startup_waits
def test_the_waits_are_stubbed_per_test_and_not_edited_out_of_the_module():
    for name, real in REAL_STARTUP_TIMEOUTS.items():
        assert getattr(windows_bridge_orchestrator, name) == real
        assert real > 0
    assert windows_bridge_orchestrator.wait_for_window_by_title is (
        win32.wait_for_window_by_title
    )


@pytest.mark.parametrize("screen", [loading_screen, transition_screen, closing_screen],
                         ids=lambda screen: screen.__name__)
def test_a_unit_test_that_starts_a_screen_over_every_monitor_fails_instead(screen):
    names_a_screen_and_runs_nothing = [sys.executable, "-c", "pass", "-m", screen.__name__]

    with pytest.raises(pytest.fail.Exception, match=screen.__name__):
        subprocess.run(names_a_screen_and_runs_nothing, check=False, timeout=60,
                       creationflags=subprocess.CREATE_NO_WINDOW)
