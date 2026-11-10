from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time

import pytest

from fun_time import (
    closing_screen,
    loading_screen,
    orchestrator,
    session_handoff,
    transition_screen,
    win32,
    windows_bridge_orchestrator,
)
from fun_time_vr import orchestrator as vr_orchestrator
from fun_time_vr import vr_runtime
from tests.conftest import TMP_ROOT
from tests.headset_refusal import his_headset_refused

STARTUP_TIMEOUTS_READ_BEFORE_ANY_FIXTURE_ZEROED_THEM = {
    "CLOSING_SCREEN_READY_CEILING_S": windows_bridge_orchestrator.CLOSING_SCREEN_READY_CEILING_S,
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
    assert windows_bridge_orchestrator.CLOSING_SCREEN_READY_CEILING_S == 0
    assert windows_bridge_orchestrator.POST_LOADING_RESOLVE_TIMEOUT_S == 0


@pytest.mark.real_startup_waits
def test_the_waits_are_stubbed_per_test_and_not_edited_out_of_the_module():
    for name, real in STARTUP_TIMEOUTS_READ_BEFORE_ANY_FIXTURE_ZEROED_THEM.items():
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


def _a_test_whose_other_thread_reaches_for_his_headset(patched: pytest.MonkeyPatch) -> None:
    def reach_and_end_on_the_refusal():
        with contextlib.suppress(RuntimeError):
            vr_runtime.probe()

    with his_headset_refused(patched):
        another_thread = threading.Thread(target=reach_and_end_on_the_refusal)
        another_thread.start()
        another_thread.join()


def test_a_reach_for_his_headset_from_another_thread_still_fails_the_test():
    with (
        pytest.MonkeyPatch.context() as patched,
        pytest.raises(pytest.fail.Exception, match="asked the VR runtime for a headset"),
    ):
        _a_test_whose_other_thread_reaches_for_his_headset(patched)


def test_a_session_run_in_this_process_serves_nothing_on_a_loopback_port():
    assert windows_bridge_orchestrator.serve_loopback(port=0) is None


@pytest.mark.parametrize("looks_it_up", [orchestrator, session_handoff, vr_orchestrator],
                         ids=lambda module: module.__name__)
def test_a_session_run_in_this_process_starts_no_heartbeat(looks_it_up, tmp_path):
    before = set(threading.enumerate())

    looks_it_up.keep_the_transition_screen(tmp_path)

    assert set(threading.enumerate()) <= before


def test_pytests_own_factory_makes_its_folders_outside_the_suites_scratch(
        tmp_path, tmp_path_factory):
    assert TMP_ROOT in tmp_path.parents
    assert TMP_ROOT not in tmp_path_factory.getbasetemp().parents
