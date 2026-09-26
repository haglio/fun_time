"""The state file a session reads its own mode off.

One small INI in the state dir holds what the whole session is *in* — which
satellites are locked, what each is filtered and ordered by, which players are in
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
from dataclasses import MISSING, dataclass, field, fields, replace
from enum import Enum
from pathlib import Path

from player_core.console import OSR2_DRIVING
from player_core.modes import MainMode, SatellitesMode, read_mode

from .audio_volume import MAX_VOLUME
from .crown import Crown
from .mode_plan import STARTUP_MAIN_MODE
from .players import Player
from .satellites_mode import STARTUP_SATELLITES_MODE


@dataclass(frozen=True)
class SatelliteState:
    """One satellite's slice of the state, by value.  Said once and held twice:
    :meth:`BridgeState.satellite` reads one satellite out, :meth:`BridgeState.with_satellite`
    writes one back, and every default is its field's own empty value."""

    locked: bool = False
    # This satellite's metadata filter query ("" = none), honored by later rebuilds.
    filter: str = ""
    # Per player, because each HUD carries its own F button; here it keeps the favorites.
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
    # player plays it.  It outlives the loop: switching a loop off leaves the map
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


def _satellite_key(name: str, player: Player) -> str:
    return f"{player.label}_{name}"


@dataclass
class BridgeState:
    # A value belonging to one satellite cannot be written to the other from here.
    portrait: SatelliteState = field(default_factory=SatelliteState)
    landscape: SatelliteState = field(default_factory=SatelliteState)
    main_mode: MainMode = STARTUP_MAIN_MODE
    # The satellite side's own mode axis (see fun_time.satellites_mode):
    # "video" is the session as ever, "origenerator" puts the hosted
    # Origenerator over the RFB and its shows over the players.
    satellites_mode: SatellitesMode = STARTUP_SATELLITES_MODE
    # Whether the hosted Origenerator is up.  Every room opens without waiting
    # out its boot, so the mode above is closed for a session's first
    # half-minute; the dispatch loop reads the app's status file and writes the
    # answer here, for the HUDs drawing the mode pair and the switch that refuses.
    origenerator_ready: bool = False
    # The main player's own F-mode -- its playlist narrowed to the scripted
    # videos -- and browse order; the satellites' are theirs.
    main_scripted_filter: bool = False
    omni_paused: bool = False
    main_latest: bool = False
    # Which shapes of video the main player's browse may reach: VR masters, flat
    # ones, or both -- the only answer outside the headset, holding one shape.
    main_plays_vr: bool = True
    main_plays_flat: bool = True
    # Genau's own, kept apart from ``main_latest`` even though the two players
    # share the main slot: ``main_latest`` describes the playlist file we built
    # for the main player, and a Genau reorder rewrites nothing of the main player's.  One flag for both
    # would light "Latest" on the main player's console over a playlist nobody reordered.
    # It alone does not resume (fun_time.session_resume.NOT_RESUMED): Genau
    # reshuffles its clips folder at every launch.
    genau_latest: bool = False
    # The player most recently navigated, as a :class:`Player` slot.  Any command
    # naming a player, or a main next/prev, updates it; the player-agnostic "active_*"
    # commands resolve against it — nav reaches all three, the satellite-only
    # actions only the two.  Starts on the main player: it is the display the eye
    # opens on, so it holds the floor until a satellite is addressed.
    active_player: int = 1
    # The main player's sound level, 0-100, and whether it is silenced.  A
    # mute leaves the level alone so a second "mute" restores what was set.
    volume: int = MAX_VOLUME
    muted: bool = False
    osr2_control: str = OSR2_DRIVING  # the console's four-button group sets it
    crowned: Crown = Crown.MAIN

    def satellite(self, player: Player) -> SatelliteState:
        """The slice of this state one satellite carries."""
        return self.portrait if Player(player) is Player.PORTRAIT else self.landscape

    def with_satellite(self, player: Player, **changes) -> BridgeState:
        """This state with one satellite's named :class:`SatelliteState` values replaced."""
        satellite = replace(self.satellite(player), **changes)
        if Player(player) is Player.PORTRAIT:
            return replace(self, portrait=satellite)
        return replace(self, landscape=satellite)


SHARED_STATE_FILENAME = "shared_bridge_state.ini"


def shared_state_path(state_dir: Path) -> Path:
    """Where *state_dir* keeps the shared state file."""
    return Path(state_dir) / SHARED_STATE_FILENAME


def _written_satellites(state: BridgeState) -> dict[str, str]:
    """Both satellites' slices, under the keys the file has always used."""
    return {
        _satellite_key(f.name, player): _written(getattr(state.satellite(player), f.name))
        for player in Player.SATELLITES
        for f in fields(SatelliteState)
    }


def _written(value: bool | int | str) -> str:
    """One value as the INI spells it."""
    if isinstance(value, bool):
        return "1" if value else "0"
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
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    tmp.replace(state_file)


def _read_satellite(section, player: Player) -> SatelliteState:
    """One satellite's slice."""
    return SatelliteState(**{
        f.name: _read_back(section.get(_satellite_key(f.name, player)), f.default)
        for f in fields(SatelliteState)
    })


def _read_back(written: str | None, default: bool | int | str) -> bool | int | str:
    """One value off the file, read as the type of *default* says.  An absent key,
    a malformed number or an unknown mode word is that default, so a file written
    before the field existed -- or hand-edited since -- is still a session."""
    if isinstance(default, bool):
        return written == "1"
    if written is None:
        return default
    if isinstance(default, Enum):
        return read_mode(type(default), written, default)
    if isinstance(default, int):
        try:
            return int(written)
        except ValueError:
            return default
    return written


# The keys this file carried until 2026-09-13, and what each became.
_LAST_SESSIONS_KEYS = {
    "locked2": "portrait_locked",
    "locked3": "landscape_locked",
    "active_side": "active_player",
    "main_f_mode": "main_scripted_filter",
    "portrait_f_mode": "portrait_favorites_filter",
    "landscape_f_mode": "landscape_favorites_filter",
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
        section.setdefault(_LAST_SESSIONS_KEYS[old], section[old])
        del section[old]
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    tmp.replace(state_file)
    return True


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
        portrait=_read_satellite(s, Player.PORTRAIT),
        landscape=_read_satellite(s, Player.LANDSCAPE),
        **{f.name: _read_back(s.get(f.name), f.default) for f in _session_fields()},
    )
