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
from dataclasses import dataclass
from pathlib import Path

from .audio_volume import MAX_VOLUME
from .mode_plan import STARTUP_MAIN_MODE
from .satellites_mode import STARTUP_SATELLITES_MODE

@dataclass
class BridgeState:
    locked2: bool = False
    locked3: bool = False
    main_mode: str = STARTUP_MAIN_MODE
    # The satellite side's own mode axis (see fun_time.satellites_mode):
    # "player" is the session as ever, "origenerator" puts the hosted
    # Origenerator over the RFB and its shows over the players.
    satellites_mode: str = STARTUP_SATELLITES_MODE
    # Whether each player is in F-mode, held per player because it is set per
    # player: each HUD carries its own button, and only the bare "f mode" (and the
    # F key) still reaches all three at once.  It narrows the satellites to the
    # favorites and the main player to the videos that have a funscript, so which
    # player it is on genuinely changes what it means.
    main_f_mode: bool = False
    portrait_f_mode: bool = False
    landscape_f_mode: bool = False
    omni_paused: bool = False
    # Which browse order each player is in: newest-first ("Latest") when set,
    # else shuffled.  Per player, since Latest and Shuffle name one, and read by
    # every later rebuild (a filter, F-mode) so that player reloads the same way.
    main_latest: bool = False
    portrait_latest: bool = False
    landscape_latest: bool = False
    # Genau's own, kept apart from ``main_latest`` even though the two players
    # share the main slot: ``main_latest`` describes the playlist file we built
    # for Nau, and a Genau reorder rewrites nothing of Nau's.  One flag for both
    # would light "Latest" on Nau's console over a playlist nobody reordered.
    # Unlike the other three it does not resume: Genau rescans and reshuffles its
    # clips folder at every launch, so a new session's Genau is not in the order
    # the last one left it in (see fun_time.session_resume.RESUMED_FIELDS).
    genau_latest: bool = False
    # The player most recently navigated (1=main/Nau, 2=portrait,
    # 3=landscape). Any portrait_/landscape_ command, or a main next/prev,
    # updates it; the side-agnostic "active_*" commands resolve against it —
    # nav (next/prev) reaches all three, the satellite-only actions only 2/3.
    # Starts on the main player: it is the display the eye opens on, so it holds the
    # floor until a satellite is addressed.
    active_side: int = 1
    # Per-satellite metadata filter queries ("" = no filter). Persisted in the shared
    # state file so they survive the dispatch loop's per-tick state resync and
    # are honored by later F-mode / reorder rebuilds.
    portrait_filter: str = ""
    landscape_filter: str = ""
    # Which group loop each satellite is running: "" none, "action" (looping the
    # action column) or "seed" (looping the seed row).  A loop is repeat-all over
    # a sub-playlist; the satellite's own auto-advance keeps it alive, but any dispatch
    # command that rebuilds or re-navigates the side drops it.  Persisted so the
    # HUD can freeze its map on the looped group and keep the loop button lit
    # while the clip auto-advances.
    portrait_loop: str = ""
    landscape_loop: str = ""
    # The clip each satellite's HUD map hangs on — the head of the queue a loop
    # wrote.  The map is ordered from it, so the group is drawn in the order the
    # player actually plays it: the clip you pressed loop on in the corner, the rest
    # walking away from it.  It outlives the loop: switching a loop off leaves the
    # map hanging here, so only the loop's own chrome goes, and the map re-homes on
    # its own once the browse moves on past the group.
    portrait_map_anchor: str = ""
    landscape_map_anchor: str = ""
    # The clip each satellite's seed row has been widened around ("more seeds").
    # While it equals the clip on screen the HUD shows the near-matches ranked in
    # alongside the family; navigating to another clip leaves it behind, so the
    # widen auto-resets without any explicit clear.
    portrait_widen_clip: str = ""
    landscape_widen_clip: str = ""
    # The clip each satellite's HUD map is frozen on for keyboard navigation ("" =
    # not navigating).  The arrow / WASD keys move a selection across that frozen
    # map, switching the satellite to each cell's clip; the anchor holds until Enter
    # locks the selection, another command re-homes the side, or the satellite
    # drifts off the map.  Persisted so the HUD freezes its map to match.
    portrait_nav_anchor: str = ""
    landscape_nav_anchor: str = ""
    # The main player's sound level, 0-100, and whether it is silenced.  A
    # mute leaves the level alone so a second "mute" restores what was set.
    volume: int = MAX_VOLUME
    muted: bool = False


SHARED_STATE_FILENAME = "shared_bridge_state.ini"


def shared_state_path(state_dir: Path) -> Path:
    """Where *state_dir* keeps the shared state file."""
    return Path(state_dir) / SHARED_STATE_FILENAME


def write_shared_state(state_file: Path, state: BridgeState) -> None:
    """Write bridge state to the shared INI file the HUD and the guard read."""
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["state"] = {
        "locked2": "1" if state.locked2 else "0",
        "locked3": "1" if state.locked3 else "0",
        "main_mode": state.main_mode,
        "satellites_mode": state.satellites_mode,
        "main_f_mode": "1" if state.main_f_mode else "0",
        "portrait_f_mode": "1" if state.portrait_f_mode else "0",
        "landscape_f_mode": "1" if state.landscape_f_mode else "0",
        "omni_paused": "1" if state.omni_paused else "0",
        "active_side": str(state.active_side),
        "portrait_filter": state.portrait_filter,
        "landscape_filter": state.landscape_filter,
        "main_latest": "1" if state.main_latest else "0",
        "genau_latest": "1" if state.genau_latest else "0",
        "portrait_latest": "1" if state.portrait_latest else "0",
        "landscape_latest": "1" if state.landscape_latest else "0",
        "portrait_loop": state.portrait_loop,
        "landscape_loop": state.landscape_loop,
        "portrait_map_anchor": state.portrait_map_anchor,
        "landscape_map_anchor": state.landscape_map_anchor,
        "portrait_widen_clip": state.portrait_widen_clip,
        "landscape_widen_clip": state.landscape_widen_clip,
        "portrait_nav_anchor": state.portrait_nav_anchor,
        "landscape_nav_anchor": state.landscape_nav_anchor,
        "volume": str(state.volume),
        "muted": "1" if state.muted else "0",
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    tmp.replace(state_file)


def _int_or(section, key: str, default: int) -> int:
    """An integer INI value, falling back to *default* when absent or malformed."""
    try:
        return int(section.get(key, default))
    except ValueError:
        return default


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
        locked2=s.get("locked2", "0") == "1",
        locked3=s.get("locked3", "0") == "1",
        main_mode=s.get("main_mode", STARTUP_MAIN_MODE),
        satellites_mode=s.get("satellites_mode", STARTUP_SATELLITES_MODE),
        main_f_mode=s.get("main_f_mode", "0") == "1",
        portrait_f_mode=s.get("portrait_f_mode", "0") == "1",
        landscape_f_mode=s.get("landscape_f_mode", "0") == "1",
        omni_paused=s.get("omni_paused", "0") == "1",
        active_side=_int_or(s, "active_side", 1),
        portrait_filter=s.get("portrait_filter", ""),
        landscape_filter=s.get("landscape_filter", ""),
        main_latest=s.get("main_latest", "0") == "1",
        genau_latest=s.get("genau_latest", "0") == "1",
        portrait_latest=s.get("portrait_latest", "0") == "1",
        landscape_latest=s.get("landscape_latest", "0") == "1",
        portrait_loop=s.get("portrait_loop", ""),
        landscape_loop=s.get("landscape_loop", ""),
        portrait_map_anchor=s.get("portrait_map_anchor", ""),
        landscape_map_anchor=s.get("landscape_map_anchor", ""),
        portrait_widen_clip=s.get("portrait_widen_clip", ""),
        landscape_widen_clip=s.get("landscape_widen_clip", ""),
        portrait_nav_anchor=s.get("portrait_nav_anchor", ""),
        landscape_nav_anchor=s.get("landscape_nav_anchor", ""),
        volume=_int_or(s, "volume", MAX_VOLUME),
        muted=s.get("muted", "0") == "1",
    )
