"""What the room looks like at the exact instant the cover lifts.

He has reported this one over and over: "when I start up Fun Time, its windows
still are not ready by the time the loading screen goes away."  Every earlier
attempt asked the question a second or two AFTER startup settled, by which time
the room has caught up and every check passes.  So this asks it at the one moment
the complaint is about — the first poll on which the loading overlay's window is
gone — and reports what was still missing then.

Two things have to be true at that instant.  Every window the session shows must
be ON SCREEN: the dashboard hides itself behind the cover and shows itself again
on a cue, and taking that cue from the cover's own departure meant the control
panel arrived seconds after the room it controls.  And the pass that puts the
room in z-order must ALREADY HAVE RUN: it is written to run behind the cover, and
a cover that leaves first turns it into a room sorting itself out in front of him.

Whether anything is COVERING those windows is a separate question, and one
``test_loading_screen_startup_integration`` already asks of the satellites.  It is
left there: the dashboard this session runs with brings its notice overlay, whose
whole job is to flash over a player for a couple of seconds, so a coverage check
here would go red on a toast doing exactly what it is for.
"""
from __future__ import annotations

import shutil
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
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

# The windows a session that opens in nau mode puts on screen, by exact title.
# Genau is deliberately absent: nau mode parks it, and a parked window is not a
# window that failed to arrive.
SHOWN_TITLES = ("Fun Time", "Portrait AI Player", "Landscape AI Player", "Nau")

# What the orchestrator logs when the room has been banded and settled.
BANDED = "Post-loading window state corrected"

_POLL_S = 0.02


class _AtTheReveal(threading.Thread):
    """Watch for the cover's window to go, and photograph the room the instant it
    does.

    A thread because the reveal happens deep inside ``session.start()``, which
    does not return until the bridge is fully up — long after the moment this
    test is about.
    """

    def __init__(self, timeout_s: float = 240.0) -> None:
        super().__init__(daemon=True)
        self._timeout_s = timeout_s
        self.cover_was_up = False
        self.lifted_at = 0.0
        self.hwnds: dict[str, int] = {}

    def run(self) -> None:
        deadline = time.monotonic() + self._timeout_s
        while time.monotonic() < deadline:
            if find_window_by_title(LOADING_SCREEN_TITLE, exact=True):
                self.cover_was_up = True
            elif self.cover_was_up:
                break
            time.sleep(_POLL_S)
        else:
            return
        # One photograph, taken as close to the reveal as a 20ms poll allows.
        # Everything asserted on comes out of this instant: asking again later is
        # asking a different question, and it is the one that kept passing.
        # Wall clock, so it can be read against the orchestrator's own log.
        self.lifted_at = time.time()
        self.hwnds = {
            title: find_window_by_title(title, exact=True) for title in SHOWN_TITLES
        }


def test_the_room_is_finished_when_the_cover_lifts():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    watcher = _AtTheReveal()
    watcher.start()
    try:
        # The dashboard is the window this is mostly about, so this is one of the
        # few sessions that runs with it enabled.  Faked side-by-side monitors for
        # the reason the other overlay test fakes them: on the hidden desktop's
        # single screen the real layout collapses every window onto it, and a
        # player landing on top of another one is then the layout's doing rather
        # than the choreography's.
        session.start(wait_seconds=180.0, env_overrides={
            "FUN_TIME_INTEGRATION_OVERLAYS": "1",
            "FUN_TIME_DISABLE_DASHBOARD": "0",
            "FUN_TIME_FAKE_MONITORS": "0,0,1280,720;1280,0,720,1440",
        })
        watcher.join(timeout=60.0)

        records, _offset = read_events(event_log_path(session.config.paths.state_dir))
        messages = [record.message for record in records]
        assert any("Loading screen launched" in m for m in messages), (
            "the session did not take the loading-screen path, so there was no "
            "cover to lift and this test proves nothing"
        )
        assert watcher.cover_was_up, "the cover never appeared"

        missing = [title for title, hwnd in watcher.hwnds.items() if not hwnd]
        assert not missing, (
            "the cover lifted on a room still missing "
            + ", ".join(missing)
            + " — those windows arrive after the reveal, which is what 'the "
            "windows are not ready when the loading screen goes away' is"
        )

        banded = [record.ts for record in records if BANDED in record.message]
        assert banded, f"the orchestrator never logged {BANDED!r}"
        assert banded[0] <= watcher.lifted_at, (
            f"the room was banded {banded[0] - watcher.lifted_at:.2f}s AFTER the "
            "cover lifted, so he watched the z-order sort itself out"
        )
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)
