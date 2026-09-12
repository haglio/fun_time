"""The state file a session reads its own mode off.

One small INI in the state dir holds what the whole session is *in* — which
sides are locked, what each is filtered and ordered by, which players are in
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
from pathlib import Path

from .audio_volume import MAX_VOLUME
from .mode_plan import MAIN_VIDEO_MODE, STARTUP_MAIN_MODE
from .players import Player
from .satellites_mode import STARTUP_SATELLITES_MODE


@dataclass(frozen=True)
class SideState:
    """One satellite's slice of the state, by value.  Said once and held twice:
    :meth:`BridgeState.side` reads one side out, :meth:`BridgeState.with_side`
    writes one back, and every default is its field's own empty value."""

    locked: bool = False
    # This side's metadata filter query ("" = none), honored by later rebuilds.
    filter: str = ""
    # Per player, because each HUD carries its own F button and F-mode narrows
    # each of the three to something different.
    f_mode: bool = False
    # Newest-first ("Latest") when set, else shuffled.  Read by every later
    # rebuild, so the side reloads the same way.
    latest: bool = False
    # Which group loop this side is running: "" none, "action" (the action
    # column) or "seed" (the seed row).  Repeat-all over a sub-playlist, kept
    # alive by the satellite's auto-advance and dropped by any rebuild.
    loop: str = ""
    # The clip this side's HUD map hangs on — the head of the queue a loop
    # wrote.  The map is ordered from it, so the group is drawn in the order the
    # player plays it.  It outlives the loop: switching a loop off leaves the map
    # hanging here, and the map re-homes once the browse moves past the group.
    map_anchor: str = ""
    # The clip this side's seed row has been widened around ("more seeds").
    # While it equals the clip on screen the HUD ranks the near-matches in
    # alongside the family; navigating away strands it, so the widen resets.
    widen_clip: str = ""
    # The clip this side's HUD map is frozen on for keyboard navigation ("" =
    # not navigating).  The arrow / WASD keys move a selection across that frozen
    # map; the anchor holds until Enter locks the selection, another command
    # re-homes the side, or the satellite drifts off the map.
    nav_anchor: str = ""


def _side_key(name: str, player: Player) -> str:
    """The INI key one satellite's *name* value is written under — a wire
    spelling, not the record's.  ``locked`` goes out as ``locked2``/``locked3``
    because that is what every session before this one wrote."""
    return f"locked{int(player)}" if name == "locked" else f"{player.label}_{name}"


@dataclass
class BridgeState:
    # A value belonging to one side cannot be written to the other from here.
    portrait: SideState = field(default_factory=SideState)
    landscape: SideState = field(default_factory=SideState)
    main_mode: str = STARTUP_MAIN_MODE
    # The satellite side's own mode axis (see fun_time.satellites_mode):
    # "video" is the session as ever, "origenerator" puts the hosted
    # Origenerator over the RFB and its shows over the players.
    satellites_mode: str = STARTUP_SATELLITES_MODE
    # The main player's own F-mode and browse order; the satellites' are theirs.
    main_f_mode: bool = False
    omni_paused: bool = False
    main_latest: bool = False
    # Which shapes of video the main player's browse may reach: VR masters, flat
    # ones, or both -- the only answer outside the headset, holding one shape.
    main_plays_vr: bool = True
    main_plays_flat: bool = True
    # Genau's own, kept apart from ``main_latest`` even though the two players
    # share the main slot: ``main_latest`` describes the playlist file we built
    # for Nau, and a Genau reorder rewrites nothing of Nau's.  One flag for both
    # would light "Latest" on Nau's console over a playlist nobody reordered.
    # It alone does not resume (fun_time.session_resume.NOT_RESUMED): Genau
    # reshuffles its clips folder at every launch.
    genau_latest: bool = False
    # The player most recently navigated, as a :class:`Player` slot.  Any sided
    # command, or a main next/prev, updates it; the side-agnostic "active_*"
    # commands resolve against it — nav reaches all three, the satellite-only
    # actions only the two.  Starts on the main player: it is the display the eye
    # opens on, so it holds the floor until a satellite is addressed.
    active_side: int = 1
    # The main player's sound level, 0-100, and whether it is silenced.  A
    # mute leaves the level alone so a second "mute" restores what was set.
    volume: int = MAX_VOLUME
    muted: bool = False

    def side(self, player: Player) -> SideState:
        """The slice of this state one satellite carries."""
        return self.portrait if Player(player) is Player.PORTRAIT else self.landscape

    def with_side(self, player: Player, **changes) -> BridgeState:
        """This state with one satellite's named :class:`SideState` values replaced."""
        side = replace(self.side(player), **changes)
        if Player(player) is Player.PORTRAIT:
            return replace(self, portrait=side)
        return replace(self, landscape=side)


SHARED_STATE_FILENAME = "shared_bridge_state.ini"


def shared_state_path(state_dir: Path) -> Path:
    """Where *state_dir* keeps the shared state file."""
    return Path(state_dir) / SHARED_STATE_FILENAME


def _written_sides(state: BridgeState) -> dict[str, str]:
    """Both satellites' slices, under the keys the file has always used."""
    return {
        _side_key(f.name, player): _written(getattr(state.side(player), f.name))
        for player in Player.SATELLITES
        for f in fields(SideState)
    }


def _written(value: bool | int | str) -> str:
    """One value as the INI spells it."""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _session_fields():
    """The record's own fields.  A satellite's are :class:`SideState`'s, and are
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
        **_written_sides(state),
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    tmp.replace(state_file)


_RESUMED_MAIN_MODES = {"nau": MAIN_VIDEO_MODE, "hybrid": MAIN_VIDEO_MODE}
_RESUMED_SATELLITES_MODES = {"player": "video"}


def _resumed_main_mode(saved: str) -> str:
    return _RESUMED_MAIN_MODES.get(saved, saved)


def _resumed_satellites_mode(saved: str) -> str:
    return _RESUMED_SATELLITES_MODES.get(saved, saved)


def _read_side(section, player: Player) -> SideState:
    """One satellite's slice."""
    return SideState(**{
        f.name: _read_back(section.get(_side_key(f.name, player)), f.default)
        for f in fields(SideState)
    })


def _read_back(written: str | None, default: bool | int | str) -> bool | int | str:
    """One value off the file, read as the type of *default* says.  An absent key
    or a malformed number is that default, so a file written before the field
    existed -- or hand-edited since -- is still a session to come back to."""
    if isinstance(default, bool):
        return written == "1"
    if written is None:
        return default
    if isinstance(default, int):
        try:
            return int(written)
        except ValueError:
            return default
    return written


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
    carried = {f.name: _read_back(s.get(f.name), f.default) for f in _session_fields()}
    return BridgeState(
        portrait=_read_side(s, Player.PORTRAIT),
        landscape=_read_side(s, Player.LANDSCAPE),
        # The two mode words are the only values not carried verbatim: a session
        # saved under a name the app has dropped comes back as what it became.
        **{**carried,
           "main_mode": _resumed_main_mode(carried["main_mode"]),
           "satellites_mode": _resumed_satellites_mode(carried["satellites_mode"])},
    )
