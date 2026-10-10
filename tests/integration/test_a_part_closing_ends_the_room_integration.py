"""A part of the room that closes while the session is up takes the whole room
down with it, on a real session.

His rule (2026-10-08): "If one component of Fun Time dies, the whole thing
should die." The unit tests hold the decision; this holds that a real session
acts on it: a player that goes is noticed, and every other window goes too.
"""
from __future__ import annotations

import sys
import time

import pytest

from fun_time.process_tree import kill_process_tree
from fun_time.win32_process import get_process_creation_time

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)
from .run_clock import Budget

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

ROOM_CLOSES_WITHIN_S = 30.0


def _still_up(child) -> bool:
    return bool(child.created_at) and get_process_creation_time(child.pid) == child.created_at


def test_a_player_closing_mid_session_closes_the_whole_room():
    config_path = build_integration_config(build_integration_temp_root())
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        children = session.read_child_processes()
        kill_process_tree(children["landscape_pid"].pid)

        session._wait_for_own_log(
            "The session ended because the Landscape player closed itself",
            after=session._log_pos, timeout=ROOM_CLOSES_WITHIN_S)
        budget = Budget(ROOM_CLOSES_WITHIN_S)
        while not budget.expired() and any(map(_still_up, children.values())):
            time.sleep(0.5)

        assert [key for key, child in children.items() if _still_up(child)] == []
    finally:
        session.stop()
