"""Who has the OSR2, moment to moment — and whether anyone does.

The Robot Hand and a funscript both feed the broker's one UDP T-Code inlet, so only one
may drive at a time.  This is the arbiter that hands the device between them —
edge-triggered on the main player's published status, and asserted rather than
fired-and-forgotten, because a verb queued on a file channel can still die.

Above it sits the console's own four-state switch: parked, retracted and
control off are nobody driving, and this is what carries them out.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from player_core.console import (
    OSR2_CONTROL_OFF,
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
)
from player_core.file_channel import append_command
from player_core.funscript import PARK_TOUCH_WAIT_CAP_MS

from .mode_plan import main_player_displays
from .player_status import read_main_player_status

# How often the standing pair (SET_TCODE_ENABLED + PAUSE/RESUME) is re-queued
# without an edge, so a verb lost in transit converges instead of staying lost
# until the next turn boundary.
REASSERT_S = 1.0

TCODE_OFF = "SET_TCODE_ENABLED 0"
TCODE_ON = "SET_TCODE_ENABLED 1"

_HELD = (OSR2_PARKED, OSR2_RETRACTED)


class DeviceArbiter:
    """The video-mode handoff between the main player's funscript and the Robot Hand."""

    def __init__(
        self,
        *,
        main_player_status_file: Path,
        main_player_cmd_file: Path,
        genau_cmd_file: Path,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.main_player_status_file = main_player_status_file
        self.main_player_cmd_file = main_player_cmd_file
        self.genau_cmd_file = genau_cmd_file
        self._clock = clock
        # Whether the funscript is driving the OSR2 right now (so the hand is
        # paused and the main player's T-Code is on) or the Robot Hand is (a funscript gap
        # or an unscripted video).  None means "no decision applied yet" — set
        # outside video mode so re-entry re-asserts the correct driver.
        self._funscript_driving: bool | None = None
        self._main_player_status = None
        # When the park-touch hold releases the pending hand-to-script flip;
        # None outside one — see _holding_for_park_touch.
        self._park_touch_deadline: float | None = None
        # The control state last carried out, None while somebody is driving.
        self._asserted_control: str | None = None
        self._asserted_at: float = 0.0
        # Whether a hold left Genau's output switched off, so a driver taking the
        # device back knows to switch it on again.
        self._muted = False
        self._stopped_by_control_off = False

    def sync(self, main_mode: str, *, paused: bool,
             control: str = OSR2_DRIVING) -> None:
        """In video mode, route the OSR2 to the funscript or the Robot Hand,
        moment to moment.

        *control* off, parked or retracted is nobody driving, in every mode, and
        nothing below runs: there is no device to hand over.

        The funscript drives while it is actively scripting (``has_funscript``
        and not ``funscript_resting``); the hand drives the unscripted stretches.
        Each handoff sets both levers: the main player's T-Code on + the hand paused for the
        funscript, or the main player's T-Code off (so its gap drift can't fight) + the hand
        resumed.  Edge-triggered, so it fires once per handoff; outside video
        mode, or paused, the remembered state is cleared so re-entry re-asserts.

        The handoff itself is not smoothed here, and nothing waits for the
        motion: whoever takes the device walks it from where it is to where it
        needs to be (the main player's driver parks it over its handoff ramp; the hand climbs
        back out of the park over the same one).  Waiting here for the hand's next
        floor-touch made the moment depend on the live motion, and the trace —
        which had to draw that moment before it happened — could only guess it.
        """
        if control in (OSR2_CONTROL_OFF, *_HELD):
            self._carry_out(control)
            self._funscript_driving = None
            self._park_touch_deadline = None
            return
        self._asserted_control = None
        self._hand_the_output_back()
        self._start_what_control_off_stopped(main_mode, paused=paused)
        if not main_player_displays(main_mode) or paused:
            self._funscript_driving = None
            self._park_touch_deadline = None
            return
        previous = self._main_player_status
        status = read_main_player_status(self.main_player_status_file, fallback=previous)
        self._main_player_status = status
        funscript_driving = status.funscript_driving
        now = self._clock()
        if (funscript_driving == self._funscript_driving
                and now - self._asserted_at < REASSERT_S):
            return
        if funscript_driving and self._funscript_driving is False:
            # Taking the device FROM the hand: a motion whose floor rests ON the
            # park is set down where the trace draws its blue ending, on its next
            # touch-down, so the flip holds for that one touch; a raised floor
            # takes the ramp and flips at once.  Only a FLOWING crossing holds --
            # entered by a seek there is no drawn ending to honor, and a hold
            # there kept the hand swinging under a green picture for its cap.
            flowed = (previous is not None
                      and abs(status.position_ms - previous.position_ms) < 1_500)
            if flowed and self._holding_for_park_touch(now, status):
                return
        else:
            self._park_touch_deadline = None
        # The edge is recorded only once both verbs actually queued.
        queued_main_player = append_command(
            self.main_player_cmd_file,
            TCODE_ON if funscript_driving else TCODE_OFF,
        )
        queued_genau = append_command(
            self.genau_cmd_file,
            "PAUSE" if funscript_driving else "RESUME",
        )
        if queued_main_player and queued_genau:
            self._funscript_driving = funscript_driving
            self._asserted_at = now
            self._park_touch_deadline = None

    def _carry_out(self, control: str) -> None:
        """Hold the device where *control* says against both engines, asserted on
        the heartbeat the way the handoff pair is."""
        now = self._clock()
        if self._asserted_control == control and now - self._asserted_at < REASSERT_S:
            return
        genau = ("RESUME", TCODE_OFF) if control in _HELD else ("PAUSE", "PARK")
        queued = [append_command(self.main_player_cmd_file, TCODE_OFF)]
        queued += [append_command(self.genau_cmd_file, verb) for verb in genau]
        if all(queued):
            self._asserted_control, self._asserted_at = control, now
            self._muted = self._muted or control in _HELD
            self._stopped_by_control_off = control == OSR2_CONTROL_OFF

    def _hand_the_output_back(self) -> None:
        """Switch Genau's output on again the moment somebody is driving, in
        whatever mode the hold was let go in."""
        if self._muted and append_command(self.genau_cmd_file, TCODE_ON):
            self._muted = False

    def _start_what_control_off_stopped(self, main_mode: str, *, paused: bool) -> None:
        if not self._stopped_by_control_off or paused:
            return
        if main_player_displays(main_mode) or append_command(self.genau_cmd_file, "RESUME"):
            self._stopped_by_control_off = False

    def _holding_for_park_touch(self, now: float, status) -> bool:
        """Whether the hand-to-script flip is still waiting for a touch-down.

        The touch is THE MAIN PLAYER'S CHOICE, published with its status: the trace picks
        one touch-down, draws the blue ending on it, and this side simply ends
        the hand's turn when the playhead reaches it — one chooser, so the device
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
