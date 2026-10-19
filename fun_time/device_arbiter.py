"""Who may send the OSR2 its T-Code, moment to moment: stated whole, whenever it
changes, to every party that may be sending -- each Funestra whose funscript has
had the OSR2, and the Robot Hand."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app_support import osr2_hold
from funestra.console import HOLD_VERB, OSR2_DRIVING
from funestra.file_channel import append_command
from funestra.funscript import PARK_TOUCH_WAIT_CAP_MS

from .funestra_status import ScriptedStatus, read_main_funestra_status
from .mode_plan import kino_shows
from .room_funestras import RoomFunestra
from .satellite_control import read_satellite_status

TCODE_OFF = "SET_TCODE_ENABLED 0"
TCODE_ON = "SET_TCODE_ENABLED 1"

# Further than this between two readings is a seek into the script, not a crossing.
_FLOWED_MS = 1_500


@dataclass(frozen=True)
class SatelliteLine:
    status_file: Path
    cmd_file: Path


@dataclass(frozen=True)
class Osr2Turn:
    script: RoomFunestra | None = None
    hand_playing: bool = True
    hand_heard: bool = True
    held_at: str | None = None


class DeviceArbiter:
    def __init__(
        self,
        *,
        main_funestra_status_file: Path,
        main_funestra_cmd_file: Path,
        genau_cmd_file: Path,
        device_on: Callable[[], bool],
        satellites: Mapping[RoomFunestra, SatelliteLine] = MappingProxyType({}),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.main_funestra_status_file = main_funestra_status_file
        self.main_funestra_cmd_file = main_funestra_cmd_file
        self.genau_cmd_file = genau_cmd_file
        self._device_on = device_on
        self._satellites = dict(satellites)
        self._clock = clock
        self._may_be_sending = {RoomFunestra.MAIN}
        self._statuses: dict[RoomFunestra, ScriptedStatus] = {}
        self._hand_has_the_stretch_of: RoomFunestra | None = None
        self._park_touch_deadline: float | None = None
        self._stated: Osr2Turn | None = None

    def sync(self, main_mode: str, *, paused: bool,
             control: str = OSR2_DRIVING, holder: RoomFunestra = RoomFunestra.MAIN) -> None:
        turn = self._turn(main_mode, paused=paused, control=control, holder=holder)
        if turn != self._stated and self._state(turn):
            self._stated = turn

    def _turn(self, main_mode: str, *, paused: bool, control: str,
              holder: RoomFunestra) -> Osr2Turn:
        if control in HOLD_VERB:
            return self._with_no_handoff(Osr2Turn(
                hand_playing=not paused, hand_heard=False,
                held_at=self._held_at(HOLD_VERB[control])))
        if paused:
            return self._with_no_handoff(Osr2Turn(
                hand_playing=False, held_at=self._held_at(osr2_hold.PARK)))
        if holder is RoomFunestra.MAIN and not kino_shows(main_mode):
            return self._with_no_handoff(Osr2Turn())
        self._may_be_sending.add(holder)
        if self._script_takes_it(holder):
            return Osr2Turn(script=holder, hand_playing=False)
        return Osr2Turn()

    def _held_at(self, hold: str) -> str | None:
        return hold if self._device_on() else None

    def _with_no_handoff(self, turn: Osr2Turn) -> Osr2Turn:
        self._hand_has_the_stretch_of = None
        self._park_touch_deadline = None
        return turn

    def _script_takes_it(self, holder: RoomFunestra) -> bool:
        previous = self._statuses.get(holder)
        status = self._read_status(holder, previous)
        self._statuses[holder] = status
        crossing = (status.funscript_driving and self._hand_has_the_stretch_of is holder
                    and previous is not None
                    and abs(status.position_ms - previous.position_ms) < _FLOWED_MS)
        holding = crossing and self._holding_for_park_touch(status)
        if not holding:
            self._park_touch_deadline = None
        takes_it = status.funscript_driving and not holding
        self._hand_has_the_stretch_of = None if takes_it else holder
        return takes_it

    def _state(self, turn: Osr2Turn) -> bool:
        """Every stop before any start, and no start until every stop is queued."""
        stops, starts = self._stops_and_starts(turn)
        stopped = [append_command(path, verb) for path, verb in stops]
        if not all(stopped):
            return False
        started = [append_command(path, verb) for path, verb in starts]
        return all(started)

    def _stops_and_starts(self, turn: Osr2Turn) -> tuple[list[tuple[Path, str]], ...]:
        genau = self.genau_cmd_file
        stops = [(self._cmd_file(funestra), TCODE_OFF) for funestra in RoomFunestra
                 if funestra in self._may_be_sending and funestra is not turn.script]
        starts: list[tuple[Path, str]] = []
        if turn.hand_heard:
            starts.append((genau, TCODE_ON))
        else:
            stops.append((genau, TCODE_OFF))
        if turn.hand_playing:
            starts.append((genau, "RESUME"))
        else:
            stops.append((genau, "PAUSE"))
        if turn.held_at is not None:
            starts.append((genau, turn.held_at))
        if turn.script is not None:
            starts.append((self._cmd_file(turn.script), TCODE_ON))
        return stops, starts

    def _cmd_file(self, funestra: RoomFunestra) -> Path:
        if funestra is RoomFunestra.MAIN:
            return self.main_funestra_cmd_file
        return self._satellites[funestra].cmd_file

    def _read_status(self, funestra: RoomFunestra, previous) -> ScriptedStatus:
        if funestra is RoomFunestra.MAIN:
            return read_main_funestra_status(self.main_funestra_status_file, fallback=previous)
        return read_satellite_status(self._satellites[funestra].status_file, fallback=previous)

    def _holding_for_park_touch(self, status: ScriptedStatus) -> bool:
        """Whether the hand-to-script flip still waits for the touch-down the holder's
        trace drew its blue ending on -- one chooser, so the device cannot stop at a
        different trough than the picture drew; capped so a stalled playhead cannot
        hold it forever."""
        touch = status.handoff_touch_ms
        if touch is None or status.position_ms >= touch:
            return False
        now = self._clock()
        if self._park_touch_deadline is None:
            self._park_touch_deadline = now + PARK_TOUCH_WAIT_CAP_MS / 1000
        return now < self._park_touch_deadline
