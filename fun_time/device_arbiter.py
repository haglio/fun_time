"""Who has the OSR2, moment to moment — and whether anyone does.

The Robot Hand and a funscript both feed the broker's one UDP T-Code inlet, so only one
may drive at a time.  This is the arbiter that hands the device between them —
edge-triggered on the published status of the player that has the OSR2, and
asserted rather than fired-and-forgotten, because a verb queued on a file
channel can still die.

Above it sits the console's own four-state switch: parked, retracted and
control off are nobody driving, and this is what carries them out.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from player_core.console import (
    OSR2_CONTROL_OFF,
    OSR2_DRIVING,
)
from player_core.file_channel import append_command
from player_core.funscript import PARK_TOUCH_WAIT_CAP_MS

from .broker_control import HOLD_VERB, PARK_CMD
from .mode_plan import main_player_displays
from .player_status import ScriptedStatus, read_main_player_status
from .players import Player
from .satellite_control import read_satellite_status

# How often the standing pair (SET_TCODE_ENABLED + PAUSE/RESUME) is re-queued
# without an edge, so a verb lost in transit converges instead of staying lost
# until the next turn boundary.
REASSERT_S = 1.0

TCODE_OFF = "SET_TCODE_ENABLED 0"
TCODE_ON = "SET_TCODE_ENABLED 1"


@dataclass(frozen=True)
class SatelliteLine:
    status_file: Path
    cmd_file: Path


class DeviceArbiter:
    """The handoff between the Robot Hand and the funscript of the player that has the OSR2."""

    def __init__(
        self,
        *,
        main_player_status_file: Path,
        main_player_cmd_file: Path,
        genau_cmd_file: Path,
        satellites: Mapping[Player, SatelliteLine] = MappingProxyType({}),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.main_player_status_file = main_player_status_file
        self.main_player_cmd_file = main_player_cmd_file
        self.genau_cmd_file = genau_cmd_file
        self._satellites = dict(satellites)
        self._clock = clock
        # Which player's funscript is driving the OSR2 right now (so the hand is
        # paused and that player's T-Code is on), or that the Robot Hand is (a
        # funscript gap or an unscripted clip).  None means "no decision applied
        # yet" — set while nobody is arbitrating, so the next arbitration
        # re-asserts the correct driver.
        self._driving: tuple[Player, bool] | None = None
        self._mode: str | None = None
        self._have_had_the_device = {Player.MAIN}
        self._statuses: dict[Player, ScriptedStatus] = {}
        # When the park-touch hold releases the pending hand-to-script flip;
        # None outside one — see _holding_for_park_touch.
        self._park_touch_deadline: float | None = None
        self._carried_out: tuple[str, bool] | None = None
        self._asserted_at: float = 0.0
        # Whether a hold left Genau's output switched off, so a driver taking the
        # device back knows to switch it on again.
        self._muted = False
        self._stopped_by_control_off = False

    def sync(self, main_mode: str, *, paused: bool,
             control: str = OSR2_DRIVING, holder: Player = Player.MAIN) -> None:
        """Route the OSR2 to *holder*'s funscript or the Robot Hand, moment to
        moment -- a side player in every mode, the main player in kino mode.

        *control* off, parked or retracted is nobody driving, in every mode, and
        nothing below runs: there is no device to hand over.

        The funscript drives while it is actively scripting (``has_funscript``
        and not ``funscript_resting``); the hand drives the unscripted stretches.
        Each handoff sets both levers: the holder's T-Code on + the hand paused
        for the funscript, or the holder's T-Code off + the hand resumed, and
        every other player that has had the device told to keep off it.
        Edge-triggered, so it fires once per handoff; paused, in a new mode, or
        where the holder is not arbitrated, the remembered state is cleared so
        the next arbitration re-asserts.

        The handoff itself is not smoothed here, and nothing waits for the
        motion: whoever takes the device walks it from where it is to where it
        needs to be (the main player's driver parks it over its handoff ramp; the hand climbs
        back out of the park over the same one).  Waiting here for the hand's next
        floor-touch made the moment depend on the live motion, and the trace —
        which had to draw that moment before it happened — could only guess it.
        """
        if main_mode != self._mode:
            self._mode, self._driving = main_mode, None
        if control == OSR2_CONTROL_OFF or control in HOLD_VERB:
            self._carry_out(control, paused=paused)
            self._driving = None
            self._park_touch_deadline = None
            return
        self._carried_out = None
        self._hand_the_output_back()
        self._start_what_control_off_stopped(main_mode, paused=paused)
        if paused or (holder is Player.MAIN and not main_player_displays(main_mode)):
            self._let_the_last_driver_go(holder, paused=paused)
            self._driving = None
            self._park_touch_deadline = None
            return
        previous = self._statuses.get(holder)
        status = self._read_status(holder, previous)
        self._statuses[holder] = status
        funscript_driving = status.funscript_driving
        now = self._clock()
        if ((holder, funscript_driving) == self._driving
                and now - self._asserted_at < REASSERT_S):
            return
        if funscript_driving and self._driving == (holder, False):
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
        # The edge is recorded only once every verb actually queued.
        queued = [
            append_command(self._cmd_file(holder), TCODE_ON if funscript_driving else TCODE_OFF),
            append_command(self.genau_cmd_file, "PAUSE" if funscript_driving else "RESUME"),
            *(append_command(self._cmd_file(other), TCODE_OFF)
              for other in self._have_had_the_device if other is not holder),
        ]
        if all(queued):
            self._driving = (holder, funscript_driving)
            if funscript_driving:
                self._have_had_the_device.add(holder)
            self._asserted_at = now
            self._park_touch_deadline = None

    def _cmd_file(self, player: Player) -> Path:
        if player is Player.MAIN:
            return self.main_player_cmd_file
        return self._satellites[player].cmd_file

    def _read_status(self, player: Player, previous) -> ScriptedStatus:
        if player is Player.MAIN:
            return read_main_player_status(self.main_player_status_file, fallback=previous)
        return read_satellite_status(self._satellites[player].status_file, fallback=previous)

    def _let_the_last_driver_go(self, holder: Player, *, paused: bool) -> None:
        if self._driving is None or self._driving[0] is holder:
            return
        last_driver, its_script_drove = self._driving
        append_command(self._cmd_file(last_driver), TCODE_OFF)
        if its_script_drove and not paused:
            append_command(self.genau_cmd_file, "RESUME")

    def _carry_out(self, control: str, *, paused: bool) -> None:
        """Hold the device where *control* says against both engines, asserted on
        the heartbeat the way the handoff pair is."""
        now = self._clock()
        if self._carried_out == (control, paused) and now - self._asserted_at < REASSERT_S:
            return
        if control in HOLD_VERB:
            genau = ("PAUSE" if paused else "RESUME", TCODE_OFF, HOLD_VERB[control])
        else:
            genau = ("PAUSE", PARK_CMD)
        queued = [append_command(self._cmd_file(player), TCODE_OFF)
                  for player in self._have_had_the_device]
        queued += [append_command(self.genau_cmd_file, verb) for verb in genau]
        if all(queued):
            self._carried_out, self._asserted_at = (control, paused), now
            self._muted = self._muted or control in HOLD_VERB
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

        The touch is THE HOLDER'S CHOICE, published with its status: the trace picks
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
