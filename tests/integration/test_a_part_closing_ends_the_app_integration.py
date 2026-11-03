from __future__ import annotations

import sys
import time

import pytest
from app_support.win32 import process_started

from fun_time.process_tree import kill_process_tree

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

APP_CLOSES_WITHIN_S = 30.0


def _still_up(child) -> bool:
    return bool(child.created_at) and process_started(child.pid) == child.created_at


def test_a_funestra_closing_mid_session_closes_the_whole_room():
    config_path = build_integration_config(build_integration_temp_root())
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        children = session.read_child_processes()
        kill_process_tree(children["landscape_pid"].pid)

        session.wait_for_new_log(
            "The session ended because the Landscape Funestra closed itself",
            timeout=APP_CLOSES_WITHIN_S)
        budget = Budget(APP_CLOSES_WITHIN_S)
        while not budget.expired() and any(map(_still_up, children.values())):
            time.sleep(0.5)

        assert [key for key, child in children.items() if _still_up(child)] == []
    finally:
        session.stop()
