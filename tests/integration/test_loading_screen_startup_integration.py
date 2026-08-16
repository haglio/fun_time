"""The production startup path, curtain included, on real windows.

Every other integration session skips the loading screen (integration mode's
default), so the exact path a real session takes — launch hidden behind the
overlay, reveal, then the post-overlay z-order pass — ran only on the user's
own desktop, where "the landscape player is behind other windows on startup"
was reported and could not be reproduced by any test.  This session forces
the overlay path (``FUN_TIME_INTEGRATION_OVERLAYS=1``) and then asks the one
question that bug is about: once startup settles, is each satellite frontmost
over its own rect?  Not merely "does it carry the topmost flag" — a window
can carry WS_EX_TOPMOST and still sit buried under another topmost window
promoted after it — but the real z-order, with the covering windows named in
the failure.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

from fun_time.event_log import event_log_path
from fun_time.win32 import (
    iter_zorder,
    wait_for_window_by_title,
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

_SATELLITE_TITLES = ("Portrait AI Player", "Landscape AI Player")


def test_the_satellites_end_startup_frontmost_over_their_rects():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        # Longer than the default: this startup also boots the loading screen
        # process and holds for the reveal.  "Hotkey script started" (what
        # start waits for) comes after the post-overlay pass, so by the time
        # this returns the pass has run.
        #
        # Faked side-by-side monitors, because the hidden desktop reports one:
        # on a single screen the real layout collapses every window onto it and
        # the players legitimately overlap each other, which makes "is each
        # player frontmost over its own rect" unanswerable.  A landscape
        # primary and a portrait secondary, both inside the desktop's visible
        # area, give the plan the disjoint rects a real session has.
        session.start(wait_seconds=90.0,
                      env_overrides={
                          "FUN_TIME_INTEGRATION_OVERLAYS": "1",
                          "FUN_TIME_FAKE_MONITORS": "0,0,1280,720;1280,0,720,1440",
                      })
        # Prove the curtain actually ran — green through the overlay-less path
        # would be this test testing nothing.  The startup-phase messages land
        # in the session's event log (the windows-bridge routing begins later).
        events = event_log_path(session.config.paths.state_dir)
        text = events.read_text(encoding="utf-8", errors="replace") if events.exists() else ""
        assert "Loading screen launched" in text, (
            "the session did not take the loading-screen path; "
            "FUN_TIME_INTEGRATION_OVERLAYS did not reach the orchestrator"
        )
        assert "Post-loading window state corrected" in text

        hwnds = {
            title: wait_for_window_by_title(title, timeout_s=10, exact=True)
            for title in _SATELLITE_TITLES
        }
        for title, hwnd in hwnds.items():
            assert hwnd, f"{title} window never appeared after the reveal"

        # Settle: the reveal's last activations can still be in flight.
        time.sleep(1.0)
        stack = iter_zorder()
        # The session's logs outlive the temp root's teardown: a failure here
        # is a window-choreography failure, and the sequencer's own account of
        # what it resolved and where it moved it is the diagnosis.
        _preserve_session_logs(session)
        for title, hwnd in hwnds.items():
            covering = windows_obscuring(hwnd, stack)
            assert not covering, (
                f"{title} (hwnd={hwnd}) is covered after startup by: "
                + "; ".join(
                    f"{w.title!r} hwnd={w.hwnd} topmost={w.topmost} rect={w.rect}"
                    for w in covering
                )
            )
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


def _preserve_session_logs(session) -> None:
    """Copy the session's logs somewhere the temp-root teardown cannot reach —
    the checkout's git-ignored state dir."""
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
