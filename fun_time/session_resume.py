"""Bring a reopened session back to the clip each player was on, and the state
that shaped its playlists: ``docs/resuming-a-session.md``."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields, replace
from pathlib import Path

from player_core.file_channel import append_command
from player_core.player_verbs import LOCK_ON
from player_core.playlist import PlaylistItem, read_playlist, write_playlist

from .media_metadata import normalize_path_key
from .modes import rotated_onto, source_roots
from .player_handover import take_back_the_list
from .players import Player
from .runtime_flow import SET_LOOP_CMD
from .shared_state import (
    BridgeState,
    SatelliteState,
    migrate_shared_state,
    read_shared_state,
    write_shared_state,
)

PlaylistEntries = list[PlaylistItem]

# What a reopened session does NOT come back believing; everything else does.
# Which four of those it keeps without a file of their own, and what re-asserts
# each, is docs/resuming-a-session.md.
NOT_RESUMED = frozenset({
    "omni_paused",
    "active_player",
    "genau_latest",
    "satellites_mode",
    "origenerator_ready",
})

NOT_RESUMED_PER_SATELLITE = frozenset({"nav_anchor"})

RESUMED_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(BridgeState) if field.name not in NOT_RESUMED
)

RESUMED_SATELLITE_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(SatelliteState) if field.name not in NOT_RESUMED_PER_SATELLITE
)


def _resumed_satellite(satellite: SatelliteState) -> SatelliteState:
    return SatelliteState(**{name: getattr(satellite, name) for name in RESUMED_SATELLITE_FIELDS})


def playlist_fits_sources(playlist_file: Path, sources: str) -> bool:
    """Whether every video in *playlist_file* comes from *sources*.

    A playlist is only ever built from the source spec of the session that built
    it, so an entry from outside this one's means the file was left by the OTHER
    app sharing this state dir.
    """
    roots = source_roots(sources)
    return all(
        any(_is_within(item.path, root) for root in roots)
        for item in read_playlist(playlist_file)
    )


def _is_within(video: Path, root: Path) -> bool:
    """Whether *video* is *root* itself or sits somewhere beneath it.

    Compared component by component, on the app's normalized path key: case and
    separator differ between a library dir and a playlist naming a file in it,
    and a component match also keeps ``.../VR_old`` out of ``.../VR``.
    """
    root_parts = [normalize_path_key(part) for part in root.parts]
    video_parts = [normalize_path_key(part) for part in video.parts]
    return video_parts[: len(root_parts)] == root_parts


def playlist_opens_on(playlist_file: Path, video: str) -> bool:
    """Whether the player handed *playlist_file* will load *video*, every player
    starting at the top.  Asked before the main player's loop is handed back."""
    return playlist_leads_with(read_playlist(playlist_file), video)


def playlist_leads_with(entries: PlaylistEntries, video: str) -> bool:
    """:func:`playlist_opens_on` asked of a playlist in hand, not one on disk."""
    return bool(entries) and normalize_path_key(str(entries[0].path)) == normalize_path_key(video)


def _surviving_entries(playlist_file: Path) -> PlaylistEntries:
    """Last session's playlist, minus clips trashed or pruned since: handing mpv
    a path to nothing is how a satellite comes up stuck."""
    return [item for item in read_playlist(playlist_file) if item.path.exists()]


def resume_playlists(resumptions: Sequence[tuple[Path, str]]) -> bool:
    """Rotate each playlist file onto the video its player last had on screen.

    *resumptions* pairs a playlist file with the video named in that player's
    status file; the answer is whether there was a session to come back to.
    """
    for playlist_file, _ in resumptions:
        take_back_the_list(playlist_file)
    rotated: list[tuple[Path, PlaylistEntries]] = []
    for playlist_file, last_video in resumptions:
        entries = _surviving_entries(playlist_file)
        if not entries:
            return False
        rotated.append((playlist_file, rotated_onto(entries, last_video)))
    for playlist_file, entries in rotated:
        write_playlist(playlist_file, entries)
    return True


def resume_main_video(playlist_file: Path, video: str) -> bool:
    """Rotate a just-REBUILT main playlist onto *video*; False when it is not in
    it, which is the other half of the cross-app rebuild (docs/entering-vr.md)."""
    entries = read_playlist(playlist_file)
    rotated = rotated_onto(entries, video)
    if not playlist_leads_with(rotated, video):
        return False
    write_playlist(playlist_file, rotated)
    return True


def resume_satellite_locks(locks: Sequence[tuple[Path, bool]]) -> None:
    """Queue LOCK_ON on the command file of each satellite that was locked.

    A lock lives in the player process rather than in any file the new one
    reads, so it has to be re-sent.
    """
    for command_file, locked in locks:
        if locked:
            append_command(Path(command_file), LOCK_ON)


def resume_main_loop(main_player_cmd_file: Path, bounds: tuple[int, int] | None) -> None:
    """Queue SET_LOOP on the main player's command file for the loop it was
    running, re-sent for the same reason :func:`resume_satellite_locks` is."""
    if bounds is not None:
        append_command(Path(main_player_cmd_file), f"{SET_LOOP_CMD} {bounds[0]} {bounds[1]}")


def resume_shared_state(state_file: Path, *, resumed: bool) -> BridgeState:
    """Seed *state_file* with the state a resumed session comes back in.

    Pass *resumed* as :func:`resume_playlists` reported it.
    """
    migrate_shared_state(state_file)
    previous = read_shared_state(state_file) if resumed else None
    state = BridgeState() if previous is None else replace(
        BridgeState(**{field: getattr(previous, field) for field in RESUMED_FIELDS}),
        portrait=_resumed_satellite(previous.satellite(Player.PORTRAIT)),
        landscape=_resumed_satellite(previous.satellite(Player.LANDSCAPE)),
    )
    write_shared_state(state_file, state)
    return state
