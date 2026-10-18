"""The state file a session reads its own mode off.

One small INI in the state dir holds what the whole session is *in* — which
satellites are locked, what each is filtered and ordered by, which Funestras are in
F-mode, what is looping, where the sound is.  The dispatch loop owns it: it re-reads the
file every tick and writes it back after every command, so the state survives
its own resync and reaches the dashboard and both satellite HUDs, which are
separate processes drawing what the file says.

It lives here rather than beside the dispatch loop because startup writes it
too — seeding the session's opening mode before the loop exists (see
:func:`fun_time.session_resume.resume_shared_state`) — and the loop already
imports startup, so the file's shape has to sit under both.
"""
from __future__ import annotations

import configparser
import io
import logging
from dataclasses import MISSING, dataclass, field, fields, replace
from enum import Enum
from pathlib import Path

from funestra.console import OSR2_DRIVING
from funestra.flick_advance import FlickAdvanceState
from funestra.hud_placement import HudCorner, HudEdge
from funestra.modes import LengthMode, MainMode, SatellitesMode, read_mode
from funestra.robot_hand import FULL_INTENSITY, RobotHandState

from .audio_volume import MAX_VOLUME
from .crown import Crown
from .mode_plan import STARTUP_MAIN_MODE
from .origenerator_switches import KINO_MODE, RfbSlot, origenerator_shows
from .polled_files import READER_HOLD_BUDGET_S, replace_despite_readers
from .room_funestras import RoomFunestra

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SatelliteState:
    """One satellite's slice of the state, by value.  Said once and held twice:
    :meth:`BridgeState.satellite` reads one satellite out, :meth:`BridgeState.with_satellite`
    writes one back, and every default is its field's own empty value."""

    locked: bool = False
    hud_corner: HudCorner = HudCorner.UPPER_LEFT
    hud_edge: HudEdge = HudEdge.LOWER
    hud_minimized: bool = False
    # This satellite's metadata filter query ("" = none), honored by later rebuilds.
    filter: str = ""
    # Per Funestra, because each HUD carries its own F button; here it keeps the favorites.
    favorites_filter: bool = False
    # Newest-first ("Latest") when set, else shuffled.  Read by every later
    # rebuild, so the satellite reloads the same way.
    latest: bool = False
    # Which group loop this satellite is running: "" none, "action" (the action
    # column) or "seed" (the seed row).  Repeat-all over a sub-playlist, kept
    # alive by the satellite's auto-advance and dropped by any rebuild.
    loop: str = ""
    # The clip this satellite's HUD map hangs on — the head of the queue a loop
    # wrote.  The map is ordered from it, so the group is drawn in the order the
    # Funestra plays it.  It outlives the loop: switching a loop off leaves the map
    # hanging here, and the map re-homes once the browse moves past the group.
    map_anchor: str = ""
    # The clip this satellite's seed row has been widened around ("more seeds").
    # While it equals the clip on screen the HUD ranks the near-matches in
    # alongside the family; navigating away strands it, so the widen resets.
    widen_clip: str = ""
    # The clip this satellite's HUD map is frozen on for keyboard navigation ("" =
    # not navigating).  The arrow / WASD keys move a selection across that frozen
    # map; the anchor holds until Enter locks the selection, another command
    # re-homes the satellite, or the satellite drifts off the map.
    nav_anchor: str = ""
    mode: SatellitesMode = KINO_MODE
    speed: float = 1.0
    volume: int = MAX_VOLUME
    muted: bool = True


def _satellite_key(name: str, funestra: RoomFunestra) -> str:
    return f"{funestra.label}_{name}"


@dataclass
class BridgeState:
    # A value belonging to one satellite cannot be written to the other from here.
    portrait: SatelliteState = field(default_factory=SatelliteState)
    landscape: SatelliteState = field(default_factory=SatelliteState)
    main_mode: MainMode = STARTUP_MAIN_MODE
    rfb_slot: RfbSlot = RfbSlot.RFB
    # Whether the hosted Origenerator is up.  Every room opens without waiting
    # out its boot, so Origenerator is offered nowhere for a session's first
    # half-minute; the dispatch loop reads the app's status file and writes the
    # answer here, for whatever draws an Origenerator button and the switches
    # that refuse.
    origenerator_ready: bool = False
    # The Main Funestra's own F-mode -- its playlist narrowed to the scripted
    # videos -- and browse order; the satellites' are theirs.
    main_scripted_filter: bool = False
    main_hud_corner: HudCorner = HudCorner.UPPER_LEFT
    main_hud_edge: HudEdge = HudEdge.LOWER
    main_hud_minimized: bool = False
    omni_paused: bool = False
    main_latest: bool = False
    main_shuffle_seed: int = 0
    main_length_mode: LengthMode = LengthMode.MIXED
    # Which shapes of video the Main Funestra's browse may reach: VR masters, flat
    # ones, or both -- the only answer outside the headset, holding one shape.
    main_plays_vr: bool = True
    main_plays_flat: bool = True
    # Genau's own, kept apart from ``main_latest`` even though the two Funestras
    # share the main slot: ``main_latest`` describes the playlist file we built
    # for the Main Funestra, and a Genau reorder rewrites nothing of the Main Funestra's.  One flag for both
    # would light "Latest" on the Main Funestra's console over a playlist nobody reordered.
    genau_latest: bool = False
    genau_plays_vr: bool = True
    genau_plays_flat: bool = True
    genau_favorites_filter: bool = False
    # The Funestra most recently navigated, as a :class:`Funestra` slot.  Any command
    # naming a Funestra, or a main next/prev, updates it; the player-agnostic "active_*"
    # commands resolve against it — nav reaches all three, the satellite-only
    # actions only the two.  Starts on the Main Funestra: it is the display the eye
    # opens on, so it holds the floor until a satellite is addressed.
    active_funestra: int = 1
    # The Main Funestra's sound level, 0-100, and whether it is silenced.  A
    # mute leaves the level alone so a second "mute" restores what was set.
    volume: int = MAX_VOLUME
    muted: bool = False
    max_intensity: int = FULL_INTENSITY
    main_speed: float = 1.0
    main_locked: bool = True
    main_loop_video: str = ""
    main_loop_in_ms: int = 0
    main_loop_out_ms: int = 0
    genau_speed: int = RobotHandState().speed
    genau_amplitude: int = RobotHandState().amplitude
    genau_center: int = RobotHandState().center
    genau_shape: str = RobotHandState().shape.value
    genau_flick_seconds: int = FlickAdvanceState().interval
    genau_learned: bool = False
    genau_locked: bool = True
    mic_muted: bool = False
    headset_layout: str = ""
    osr2_control: str = OSR2_DRIVING  # the console's OSR2 switch sets it
    crowned: Crown = Crown.MAIN
    majority: Crown = Crown.PORTRAIT
    osr2_funestra: int = 1

    def satellite(self, funestra: RoomFunestra) -> SatelliteState:
        """The slice of this state one satellite carries."""
        return self.portrait if RoomFunestra(funestra) is RoomFunestra.PORTRAIT else self.landscape

    def with_satellite(self, funestra: RoomFunestra, **changes) -> BridgeState:
        """This state with one satellite's named :class:`SatelliteState` values replaced."""
        satellite = replace(self.satellite(funestra), **changes)
        if RoomFunestra(funestra) is RoomFunestra.PORTRAIT:
            return replace(self, portrait=satellite)
        return replace(self, landscape=satellite)

    def origenerator_holds_a_satellite(self) -> bool:
        return any(origenerator_shows(self.satellite(funestra).mode)
                   for funestra in RoomFunestra.SATELLITES)


SHARED_STATE_FILENAME = "shared_bridge_state.ini"


def shared_state_path(state_dir: Path) -> Path:
    """Where *state_dir* keeps the shared state file."""
    return Path(state_dir) / SHARED_STATE_FILENAME


def _written_satellites(state: BridgeState) -> dict[str, str]:
    """Both satellites' slices, under the keys the file has always used."""
    return {
        _satellite_key(f.name, funestra): _written(getattr(state.satellite(funestra), f.name))
        for funestra in RoomFunestra.SATELLITES
        for f in fields(SatelliteState)
    }


def _written(value: bool | int | float | str) -> str:
    """One value as the INI spells it."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _session_fields():
    """The record's own fields.  A satellite's are :class:`SatelliteState`'s, and are
    told apart by having no plain default -- each side is built by a factory."""
    return [f for f in fields(BridgeState) if f.default is not MISSING]


def write_shared_state(state_file: Path, state: BridgeState) -> None:
    """Write bridge state to the shared INI file the HUD and the guard read.

    Every key comes off the record itself: see
    ``test_the_round_trip_spells_no_field_of_the_record_by_hand``.
    """
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["state"] = {
        **{f.name: _written(getattr(state, f.name)) for f in _session_fields()},
        **_written_satellites(state),
    }
    _publish(state_file, parser)


def _publish(state_file: Path, parser: configparser.ConfigParser) -> None:
    text = io.StringIO()
    parser.write(text)
    if not replace_despite_readers(state_file, text.getvalue()):
        logger.warning("Could not write %s: another process kept it open for %gs",
                       state_file.name, READER_HOLD_BUDGET_S)


def _read_satellite(section, funestra: RoomFunestra) -> SatelliteState:
    """One satellite's slice."""
    return SatelliteState(**{
        f.name: _read_back(section.get(_satellite_key(f.name, funestra)), f.default)
        for f in fields(SatelliteState)
    })


def _read_back(written: str | None, default: bool | int | float | str) -> bool | int | float | str:
    """One value off the file, read as the type of *default* says.  An absent key,
    a malformed number or an unknown mode word is that default, so a file written
    before the field existed -- or hand-edited since -- is still a session."""
    if written is None:
        return default
    if isinstance(default, bool):
        return written == "1"
    if isinstance(default, Enum):
        return read_mode(type(default), written, default)
    for number in (float, int):
        if isinstance(default, number):
            try:
                return number(written)
            except ValueError:
                return default
    return written


# The keys this file once carried, and what each became.
_LAST_SESSIONS_KEYS = {
    "locked2": ("portrait_locked",),
    "locked3": ("landscape_locked",),
    "active_side": ("active_funestra",),
    "main_f_mode": ("main_scripted_filter",),
    "portrait_f_mode": ("portrait_favorites_filter",),
    "landscape_f_mode": ("landscape_favorites_filter",),
    "active_player": ("active_funestra",),
    "osr2_player": ("osr2_funestra",),
    "satellites_mode": ("portrait_mode", "landscape_mode", "rfb_slot"),
}


def migrate_shared_state(state_file: Path) -> bool:
    """Rewrite *state_file* from last session's key spelling into today's, once,
    at the startup that first reads it; the reader knows one spelling.  Returns
    whether anything was rewritten."""
    if not state_file.exists():
        return False
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    if "state" not in parser:
        return False
    section = parser["state"]
    old_keys = [old for old in _LAST_SESSIONS_KEYS if old in section]
    if not old_keys:
        return False
    for old in old_keys:
        for new in _LAST_SESSIONS_KEYS[old]:
            section.setdefault(new, section[old])
        del section[old]
    _publish(state_file, parser)
    return True


def written_with(state_file: Path, key: str) -> bool:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    return "state" in parser and key in parser["state"]


def read_shared_state(state_file: Path) -> BridgeState | None:
    """Read bridge state from the shared INI file."""
    if not state_file.exists():
        return None
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    if "state" not in parser:
        return None
    s = parser["state"]
    return BridgeState(
        portrait=_read_satellite(s, RoomFunestra.PORTRAIT),
        landscape=_read_satellite(s, RoomFunestra.LANDSCAPE),
        **{f.name: _read_back(s.get(f.name), f.default) for f in _session_fields()},
    )
