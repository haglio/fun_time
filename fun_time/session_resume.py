"""Bring a reopened session back to the clip each player was on, and the mode
they were in.

Keeping last session's playlists means keeping what SHAPED them, which is the
other half here (:func:`resume_shared_state`).  Why either half exists, what
comes back and what cannot: ``docs/resuming-a-session.md``.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields, replace
from pathlib import Path

from player_core.file_channel import append_command
from player_core.player_verbs import LOCK_ON
from player_core.playlist import PlaylistItem, read_playlist, write_playlist

from .media_metadata import normalize_path_key
from .modes import rotated_onto, source_roots
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

# What a reopened session does NOT come back believing.  Everything else does:
# most of it shaped the playlist files just resumed, and the rest is what the
# session was simply *left* in, with no more reason to reset overnight than the
# clip on screen has.
#
# Four of those have a live counterpart to re-assert, since none lives in a
# file a new process reads: the level is seeded to both audio sinks at startup
# (fun_time.audio_volume.publish_audio_level), each lock is queued back to its
# satellite (:func:`resume_satellite_locks`), startup seeds the main slot's
# players for its mode (seed_startup_states), and the device arbiter carries out
# the OSR2's control state every tick.  Carrying a flag whose
# world is not put back with it is the same lie as dropping one that was true.
#
# These are dropped because nothing carries them into the new session: OmniPause's
# flags are cleared and the OSR2 parked before the players launch, Genau reshuffles
# its clips at every launch, whichever player was last addressed is a fact about the
# session that ended, and a keyboard selection was never a thing to leave.
NOT_RESUMED = frozenset({
    "omni_paused",
    "active_player",
    "genau_latest",
    # The satellite mode is simply not remembered: every room is BUILT in video
    # mode, the hosted Origenerator being still unbuilt when the room opens, and
    # a room that switched itself over once that app arrived would take what he
    # was doing in video mode out from under him (docs/resuming-a-session.md).
    "satellites_mode",
    "origenerator_ready",
})

# The same answer for a value one satellite carries (:class:`SatelliteState`), since
# that is where the keyboard selection lives.
NOT_RESUMED_PER_SATELLITE = frozenset({"nav_anchor"})

RESUMED_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(BridgeState) if field.name not in NOT_RESUMED
)

RESUMED_SATELLITE_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(SatelliteState) if field.name not in NOT_RESUMED_PER_SATELLITE
)


def _resumed_satellite(satellite: SatelliteState) -> SatelliteState:
    """One satellite's state, minus what a new session does not bring back."""
    return SatelliteState(**{name: getattr(satellite, name) for name in RESUMED_SATELLITE_FIELDS})


def playlist_fits_sources(playlist_file: Path, sources: str) -> bool:
    """Whether every video in *playlist_file* comes from *sources*.

    A playlist is only ever built from the source spec of the session that
    built it, so an entry from outside this one's means the file was left by
    the OTHER app sharing this state dir, and the caller rebuilds rather than
    resumes (:func:`resume_main_video`).

    An unreadable or missing playlist reads as empty, and so fits vacuously:
    having nothing to resume at all is the caller's own separate answer.
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
    """Whether *playlist_file*'s first entry is *video* — which is to say,
    whether the player handed this file will load that clip, since every player
    starts at the top.  Asked before the main player's loop is handed back
    (docs/resuming-a-session.md).  Matched on the normalized key
    :func:`rotated_onto` uses: case alone is not a different file, and the
    playlist and the status file are written by different processes.
    """
    return playlist_leads_with(read_playlist(playlist_file), video)


def playlist_leads_with(entries: PlaylistEntries, video: str) -> bool:
    """:func:`playlist_opens_on` asked of a playlist in hand, not one on disk."""
    return bool(entries) and normalize_path_key(str(entries[0].path)) == normalize_path_key(video)


def _surviving_entries(playlist_file: Path) -> PlaylistEntries:
    """Last session's playlist, minus the clips that are no longer on disk.

    A playlist resumed from yesterday can name clips trashed or pruned since,
    and handing mpv a path to nothing is how a satellite comes up stuck.
    """
    return [item for item in read_playlist(playlist_file) if item.path.exists()]


def resume_playlists(resumptions: Sequence[tuple[Path, str]]) -> bool:
    """Rotate each playlist file onto the video its player last had on screen.

    *resumptions* pairs a playlist file with the video named in that player's
    status file.  Returns whether there was a session to come back to at all: a
    playlist file that is missing, or that has no clip left on disk, means there
    is not — a first run, a wiped state dir — and the caller builds fresh
    instead.  All or nothing (docs/resuming-a-session.md).
    """
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
    """Rotate a just-REBUILT main playlist onto *video*; False when it is not in it.

    The other half of the cross-app rebuild above (``docs/entering-vr.md``).
    False is an answer, not a failure: a rebuild that lacks the clip is a
    session that cannot play it, and it decides whether the loop comes back.
    """
    entries = read_playlist(playlist_file)
    rotated = rotated_onto(entries, video)
    if not playlist_leads_with(rotated, video):
        return False
    write_playlist(playlist_file, rotated)
    return True


def resume_satellite_locks(locks: Sequence[tuple[Path, bool]]) -> None:
    """Queue LOCK_ON on the command file of each satellite that was locked.

    *locks* pairs a satellite's command file with whether that side comes back
    locked.  A lock lives in the player process rather than in any file the new
    one reads, so it has to be re-sent; queued before the satellites launch, it
    drains on the first tick, over the clip the resume put at the top of the
    playlist (docs/resuming-a-session.md).
    """
    for command_file, locked in locks:
        if locked:
            append_command(Path(command_file), LOCK_ON)


def resume_main_loop(main_player_cmd_file: Path, bounds: tuple[int, int] | None) -> None:
    """Queue SET_LOOP on the main player's command file for the loop it was running.

    The main player's counterpart of :func:`resume_satellite_locks`, re-sent for
    the same reason and queued the same way (docs/resuming-a-session.md).
    *bounds* is None when there was no loop — the main player then plays the video through,
    which is already what no loop means.
    """
    if bounds is not None:
        append_command(Path(main_player_cmd_file), f"{SET_LOOP_CMD} {bounds[0]} {bounds[1]}")


def resume_shared_state(state_file: Path, *, resumed: bool) -> BridgeState:
    """Seed *state_file* with the state a resumed session comes back in.

    Pass *resumed* as :func:`resume_playlists` reported it: a session built
    fresh opens on defaults, one that kept last session's playlists keeps
    :data:`RESUMED_FIELDS`.  Written either way, and returned, since this file
    is what the dispatch loop reads its opening state from
    (docs/resuming-a-session.md).
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
