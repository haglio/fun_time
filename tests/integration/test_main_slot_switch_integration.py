from __future__ import annotations

import sys
import time

import pytest

from fun_time.win32 import (
    find_window_by_title,
    find_window_for_process,
    is_window_minimized,
    iter_zorder,
    windows_obscuring,
)

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)


@pytest.fixture(scope="module")
def shared_integration_session():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()


def _main_player(s: FunTimeIntegrationSession) -> int:
    return find_window_for_process(s.read_child_pids()["main_player_pid"], "Main Player")


def _nothing_over(hwnd: int) -> bool:
    return not windows_obscuring(hwnd, iter_zorder())


def _stays_up(hwnd: int, *, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if is_window_minimized(hwnd):
            return False
        time.sleep(0.01)
    return True


SWITCHES = (("genau_activate", "genau"), ("main_kino_activate", "kino"))


def test_a_switch_either_way_leaves_the_main_player_up_and_frontmost(
        shared_integration_session: FunTimeIntegrationSession):
    """Kino and Genau run on the one window, so a switch has nothing to park and
    nothing to stack: the Main Player stays up, stays frontmost over its rect,
    and no second window appears in the slot."""
    s = shared_integration_session
    main_player = _main_player(s)
    for command, mode in SWITCHES:
        s.write_dashboard_command(command)
        s.wait_for_new_log(f"Switched to {mode} mode")
        assert _stays_up(main_player, seconds=1.0), f"the main player was parked switching to {mode} mode"
        s.wait_until(lambda: _nothing_over(main_player),
                     description=f"the main player to be frontmost in {mode} mode")
        assert find_window_by_title("Genau") == 0, "a second window took the main slot"


def test_a_switch_while_paused_leaves_the_main_player_where_the_pause_put_it(
        shared_integration_session: FunTimeIntegrationSession):
    """OmniPause owns the window state while it holds: a switch under it changes
    what plays on the Main Player and moves the window nowhere, and leaving the
    pause brings it back frontmost."""
    s = shared_integration_session
    main_player = _main_player(s)
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("Topmost [post-enter]")
    for command, mode in SWITCHES:
        s.write_dashboard_command(command)
        s.wait_for_new_log(f"Switched to {mode} mode")
        assert _stays_up(main_player, seconds=0.5), f"the main player was parked switching to {mode} mode"

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("Topmost [post-leave]")
    s.wait_until(lambda: _nothing_over(main_player),
                 description="the main player to come back frontmost after the pause")
