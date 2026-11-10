from __future__ import annotations

import sys
import threading
import time

import pytest

from fun_time.event_log import event_log_path, read_events
from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.win32 import find_window_by_title

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
    joined_within,
)
from .run_clock import Budget

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

SHOWN_TITLES = ("Fun Time", "Portrait AI Funestra", "Landscape AI Funestra", "Main Funestra")

BANDED = "Post-loading window state corrected"

_POLL_S = 0.02


class _AtTheReveal(threading.Thread):
    def __init__(self, timeout_s: float = 240.0) -> None:
        super().__init__(daemon=True)
        self._timeout_s = timeout_s
        self.loading_screen_was_up = False
        self.lifted_at_on_the_logs_wall_clock = 0.0
        self.on_screen_though_a_message_may_cover_it: dict[str, int] = {}
        self.hidden_hwnds: dict[str, int] = {}

    def run(self) -> None:
        budget = Budget(self._timeout_s)
        while not budget.expired():
            if find_window_by_title(LOADING_SCREEN_TITLE, exact=True):
                self.loading_screen_was_up = True
            elif self.loading_screen_was_up:
                break
            time.sleep(_POLL_S)
        else:
            return
        self._photograph_before_the_session_can_catch_up()

    def _photograph_before_the_session_can_catch_up(self) -> None:
        self.lifted_at_on_the_logs_wall_clock = time.time()
        self.on_screen_though_a_message_may_cover_it = {
            title: find_window_by_title(title, exact=True) for title in SHOWN_TITLES
        }
        self.hidden_hwnds = {
            title: find_window_by_title(title, exact=True, include_hidden=True)
            for title in SHOWN_TITLES
        }


def _watching_while_start_blocks_past_the_reveal() -> _AtTheReveal:
    watcher = _AtTheReveal()
    watcher.start()
    return watcher


_WITH_THE_DASHBOARD = {"FUN_TIME_DISABLE_DASHBOARD": "0"}


def test_the_app_is_finished_when_the_loading_screen_lifts():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    watcher = _watching_while_start_blocks_past_the_reveal()
    try:
        session.start(wait_seconds=180.0, env_overrides={
            "FUN_TIME_INTEGRATION_OVERLAYS": "1",
            **_WITH_THE_DASHBOARD,
        })
        joined_within(watcher, 60.0)

        records, _offset = read_events(event_log_path(session.config.paths.state_dir))
        messages = [record.message for record in records]
        assert any("Loading screen launched" in m for m in messages), (
            "the session did not take the loading-screen path, so there was no "
            "loading screen to lift and this test proves nothing"
        )
        assert watcher.loading_screen_was_up, "the loading screen never appeared"
        assert watcher.on_screen_though_a_message_may_cover_it, (
            "the watcher never photographed Fun Time, so the loading screen's window "
            "outlived its wait — nothing here has been measured"
        )

        missing = [
            f"{title} (a hidden window of that name was up)"
            if watcher.hidden_hwnds.get(title) else f"{title} (no window at all)"
            for title, hwnd in watcher.on_screen_though_a_message_may_cover_it.items() if not hwnd
        ]
        pids = session.read_child_pids()
        assert not missing, (
            "the loading screen lifted on a session still missing "
            + ", ".join(missing)
            + " — those windows arrive after the reveal, which is what 'the "
            "windows are not ready when the loading screen goes away' is"
            + f"; the session's own children were {pids}"
        )

        banded = [record.ts for record in records if BANDED in record.message]
        assert banded, f"the orchestrator never logged {BANDED!r}"
        assert banded[0] <= watcher.lifted_at_on_the_logs_wall_clock, (
            f"the room was banded {banded[0] - watcher.lifted_at_on_the_logs_wall_clock:.2f}s AFTER the "
            "loading screen lifted, so he watched the z-order sort itself out"
        )
    finally:
        session.stop()
