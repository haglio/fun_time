"""Who has the OSR2 in hybrid, moment to moment.

Genau and a funscript both feed the broker's one UDP T-Code inlet, so only one
may drive at a time.  This is the arbiter that hands the device between them —
edge-triggered on Nau's published status, and asserted rather than
fired-and-forgotten, because a verb queued on a file channel can still die.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from player_core.file_channel import append_command
from player_core.funscript import PARK_TOUCH_WAIT_CAP_MS

from .player_status import read_nau_status

# How often the standing pair (SET_TCODE_ENABLED + PAUSE/RESUME) is re-queued
# without an edge, so a verb lost in transit converges instead of staying lost
# until the next turn boundary.
REASSERT_S = 1.0


class HybridDriver:
    """The hybrid handoff between Nau's funscript and Genau."""

    def __init__(
        self,
        *,
        nau_status_file: Path,
        nau_cmd_file: Path,
        genau_cmd_file: Path,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.nau_status_file = nau_status_file
        self.nau_cmd_file = nau_cmd_file
        self.genau_cmd_file = genau_cmd_file
        self._clock = clock
        # Whether the funscript is driving the OSR2 right now (so Genau is
        # paused and Nau's T-Code is on) or Genau is (a funscript gap or an
        # unscripted video).  None means "no decision applied yet" — set outside
        # hybrid so re-entry re-asserts the correct driver.
        self._funscript_driving: bool | None = None
        self._asserted_at: float = 0.0
        self._nau_status = None
        # When the park-touch hold releases the pending Genau-to-script flip;
        # None outside one — see _holding_for_park_touch.
        self._park_touch_deadline: float | None = None

    def sync(self, main_mode: str, *, paused: bool) -> None:
        """In hybrid, route the OSR2 to the funscript or Genau, moment to moment.

        Genau and a funscript both feed the broker's one UDP T-Code inlet, so
        only one may drive at a time.  The funscript drives while it is actively
        scripting (``has_funscript`` and not ``funscript_resting``); Genau drives
        the unscripted stretches — a video without a funscript, or a funscript's
        quiet lead-in and interior gaps.  Each handoff sets both levers: Nau's
        T-Code on + Genau paused for the funscript, or Nau's T-Code off (so its
        gap drift can't fight) + Genau resumed for Genau.  It is edge-triggered,
        so it fires once per handoff, not every tick.  Outside hybrid (or under
        omnipause) the remembered state is cleared so re-entry re-asserts the
        driver; leaving hybrid re-enables Nau's T-Code via the mode switch.

        The handoff itself is not smoothed here, and nothing waits for the
        stroke: whoever takes the device walks it from where it is to where it
        needs to be (Nau's driver parks it over its handoff ramp; Genau climbs
        back out of the park over the same one).  Waiting here for Genau's next
        floor-touch made the moment depend on the live stroke, and the trace —
        which had to draw that moment before it happened — could only guess it.
        """
        if main_mode != "hybrid" or paused:
            self._funscript_driving = None
            self._park_touch_deadline = None
            return
        previous = self._nau_status
        status = read_nau_status(self.nau_status_file, fallback=previous)
        self._nau_status = status
        funscript_driving = status.funscript_driving
        now = self._clock()
        if (funscript_driving == self._funscript_driving
                and now - self._asserted_at < REASSERT_S):
            return
        if funscript_driving and self._funscript_driving is False:
            # Taking the device FROM Genau: a stroke whose floor rests ON the
            # park is set down exactly where the trace draws its blue ending —
            # on its next touch-down — so the flip holds for that one touch.
            # A raised floor takes the ramp instead and flips at once.  Only a
            # FLOWING boundary crossing holds: entered by a seek, there is no
            # drawn blue ending to honor — the trace shows the script's turn
            # already running — and a hold there kept Genau swinging under a
            # pure green picture for its whole cap.  Nothing re-asserts during
            # a hold; the standing pair still says Genau, which is the truth
            # of it.
            flowed = (previous is not None
                      and abs(status.position_ms - previous.position_ms) < 1_500)
            if flowed and self._holding_for_park_touch(now, status):
                return
        else:
            self._park_touch_deadline = None
        # ASSERTED, not fired-and-forgotten.  A verb queued on a file channel
        # can still die — a writer replacing the file whole, a drain racing the
        # append, a locked file exhausting the retries — and an edge-triggered
        # arbiter that assumed delivery left the session split-brained for a
        # whole cluster: Genau paused, the funscript never enabled, everything
        # idle and grey.  So the edge is recorded only once both verbs actually
        # queued, and the standing pair is re-queued on a slow heartbeat — both
        # verbs are idempotent at their players — so any lost one converges
        # within a second instead of at the next turn boundary.
        queued_nau = append_command(
            self.nau_cmd_file,
            "SET_TCODE_ENABLED 1" if funscript_driving else "SET_TCODE_ENABLED 0",
        )
        queued_genau = append_command(
            self.genau_cmd_file,
            "PAUSE" if funscript_driving else "RESUME",
        )
        if queued_nau and queued_genau:
            self._funscript_driving = funscript_driving
            self._asserted_at = now
            self._park_touch_deadline = None

    def _holding_for_park_touch(self, now: float, status) -> bool:
        """Whether the Genau-to-script flip is still waiting for a touch-down.

        The touch is NAU'S CHOICE, published with its status: the trace picks
        one touch-down, draws the blue ending on it, and this side simply ends
        Genau's turn when the playhead reaches it — one chooser, so the device
        cannot stop at a different trough than the picture drew.  When each
        side chose from its own read of the wave, the arbiter could take an
        earlier touch, and the leftover drawn blue vanished the moment the dot
        reached it.  No published touch means the ramp case: flip at once.
        The wall-clock cap keeps a stalled playhead from holding forever.
        """
        touch = status.handoff_touch_ms
        if touch is None or status.position_ms >= touch:
            self._park_touch_deadline = None
            return False
        if self._park_touch_deadline is None:
            self._park_touch_deadline = now + PARK_TOUCH_WAIT_CAP_MS / 1000
        if now < self._park_touch_deadline:
            return True
        self._park_touch_deadline = None
        return False
