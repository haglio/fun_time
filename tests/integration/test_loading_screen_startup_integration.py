from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from fun_time.event_log import event_log_path
from fun_time.win32 import (
    describe_windows,
    iter_zorder,
    windows_obscuring,
)

from .integration_support import (
    FunTimeIntegrationSession,
    await_window,
    build_integration_config,
    build_integration_temp_root,
    let_seconds_pass,
)

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="Fun Time integration tests require Windows",
    ),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

_SATELLITE_TITLES = ("Portrait AI Funestra", "Landscape AI Funestra")

_STARTUP_UNDER_A_LOADING_SCREEN_S = 90.0


def _this_startup_took_the_loading_screen_path(session) -> None:
    events = event_log_path(session.config.paths.state_dir)
    text = events.read_text(encoding="utf-8", errors="replace") if events.exists() else ""
    assert "Loading screen launched" in text, (
        "the session did not take the loading-screen path; "
        "FUN_TIME_INTEGRATION_OVERLAYS did not reach the orchestrator"
    )
    assert "Post-loading window state corrected" in text, (
        "start returned before the post-loading pass had run, so the z-order read next "
        "says nothing about it"
    )


def test_the_satellites_end_startup_frontmost_over_their_rects_not_merely_topmost():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start(wait_seconds=_STARTUP_UNDER_A_LOADING_SCREEN_S,
                      env_overrides={"FUN_TIME_INTEGRATION_OVERLAYS": "1"})
        _this_startup_took_the_loading_screen_path(session)

        hwnds = {
            title: await_window(title, 10, exact=True)
            for title in _SATELLITE_TITLES
        }
        for title, hwnd in hwnds.items():
            assert hwnd, f"{title} window never appeared after the reveal"

        let_seconds_pass(1.0)
        stack = iter_zorder()
        for title, hwnd in hwnds.items():
            covering = windows_obscuring(hwnd, stack)
            if covering:
                _preserve_session_logs(session)
            assert not covering, (
                f"{title} (hwnd={hwnd}) is covered after startup by: {describe_windows(covering)}")
    finally:
        session.stop()


def _preserve_session_logs(session) -> None:
    keep = Path(__file__).resolve().parents[2] / "state" / "loading_screen_test_logs"
    keep.mkdir(parents=True, exist_ok=True)
    state_dir = session.config.paths.state_dir
    for candidate in (session.windows_bridge_log, session.orchestrator_log,
                      event_log_path(state_dir)):
        try:
            if candidate.exists():
                shutil.copy2(candidate, keep / candidate.name)
        except OSError:
            pass
