"""Bring a reopened session back to the clip each player was on, and the mode
they were in.

Every player starts at the top of the playlist file fun_time hands it, and
startup used to overwrite all three with a fresh weighted shuffle — so
reopening Fun Time landed on three clips you had never chosen and lost whatever
you were watching.  Resume instead keeps last session's playlists and rotates
each one onto the clip that was on screen: the player's first entry is where
you left off, and because a playlist wraps, the clips that were coming up still
come up in the same order.

Keeping those files means keeping what SHAPED them, which is the other half here
(:func:`resume_shared_state`): a playlist carries its session's F-mode, filter,
order and loop in it, so the session has to come back believing what its files
say, or every HUD describes a session other than the one playing.

Nothing has to be written at shutdown for either half.  Each player already
publishes the video it is playing to its status file every tick, and the
dispatch loop writes the state file after every command, so the last tick before
the session ended is the record — one that survives the force-kill that ends a
session, and a crash or a power cut too, where a shutdown hook would not.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from player_core.file_channel import append_command
from player_core.playlist import read_playlist

from .media_metadata import normalize_path_key
from .modes import source_roots, write_playlist_entries
from .runtime_flow import SET_LOOP_CMD
from .shared_state import BridgeState, read_shared_state, write_shared_state

PlaylistEntries = list[tuple[Path, Path | None]]

# What a reopened session comes back believing.  Most of it is what shaped the
# playlist files that were just resumed — each player's own F-mode and each
# side's filter decide which clips are in them, Latest fixes their order, and a
# group loop IS the group written out as the playlist, with the map anchored
# (and the seed row widened) on the clip it started from.  The rest is what the
# session was simply *left* in: the sound level and each side's lock are how you
# had it set, and there is no more reason for them to reset overnight than there
# is for the clip on screen to.
#
# Three of them have a live counterpart to re-assert, since none lives in a file
# a new process reads: the level is seeded to both audio sinks at startup (see
# fun_time.audio_volume.publish_audio_level), each lock is queued back to its
# satellite (:func:`resume_satellite_locks`), and the main slot's mode is what
# startup seeds the two main-slot players and their windows for (see
# fun_time.windows_bridge_startup.seed_startup_states).  Carrying a flag whose
# world is not put back with it is the same lie as dropping one that was true.
#
# Nothing else survives, because nothing carries it into the new session:
# OmniPause's paused flags are cleared before the players launch, and a
# keyboard-navigation selection was never a thing you could leave running.
RESUMED_FIELDS = (
    "main_mode",
    # The satellites' own mode axis comes back the way main_mode does.
    "satellites_mode",
    "main_f_mode",
    "portrait_f_mode",
    "landscape_f_mode",
    "portrait_filter",
    "landscape_filter",
    "main_latest",
    "portrait_latest",
    "landscape_latest",
    "portrait_loop",
    "landscape_loop",
    "portrait_map_anchor",
    "landscape_map_anchor",
    "portrait_widen_clip",
    "landscape_widen_clip",
    "locked2",
    "locked3",
    "volume",
    "muted",
)


def playlist_fits_sources(playlist_file: Path, sources: str) -> bool:
    """Whether every video in *playlist_file* comes from *sources*.

    A playlist is only ever built from the source spec of the session that
    built it, so an entry from outside this session's spec means the file was
    left by a DIFFERENT app sharing this state dir — today FunTimeVR, whose
    main rotation merges the VR library into this one's.  Resuming that is
    how VR videos reached the desktop app's main player, which must never
    play them, so the caller rebuilds rather than resumes.

    An unreadable or missing playlist reads as empty, and so fits vacuously:
    there is nothing foreign in it, and having nothing to resume at all is the
    caller's own separate answer.
    """
    roots = source_roots(sources)
    return all(
        any(_is_within(video, root) for root in roots)
        for video, _funscript in read_playlist(playlist_file)
    )


def _is_within(video: Path, root: Path) -> bool:
    """Whether *video* is *root* itself or sits somewhere beneath it.

    Compared component by component, on the same normalized key the rest of the
    app matches paths by: a library dir and the playlist naming a file in it
    can differ in case and in separator on Windows, and neither difference is a
    different library.  Matching on components also keeps a sibling dir whose
    name merely starts the same — ``.../VR_old`` beside ``.../VR`` — outside.
    """
    root_parts = [normalize_path_key(part) for part in root.parts]
    video_parts = [normalize_path_key(part) for part in video.parts]
    return video_parts[: len(root_parts)] == root_parts


def playlist_opens_on(playlist_file: Path, video: str) -> bool:
    """Whether *playlist_file*'s first entry is *video*.

    Which is to say: whether the player handed this file will actually load that
    clip, since every player starts at the top.  Asked of the main player before its
    loop is handed back — a loop is a range inside one video, and a resume that
    could not rotate onto that video (deleted since, or the whole playlist
    rebuilt) would otherwise put those bounds on whatever leads instead.

    Matched on the same normalized key :func:`_rotate_onto` compares by, and for
    the same reason: the playlist and the status file are written by different
    processes, and case alone is not a different file on Windows.
    """
    entries = read_playlist(playlist_file)
    return bool(entries) and normalize_path_key(str(entries[0][0])) == normalize_path_key(video)


def _surviving_entries(playlist_file: Path) -> PlaylistEntries:
    """Last session's playlist, minus the clips that are no longer on disk.

    A playlist built moments before launch could only name files that were
    there; one resumed from yesterday can name clips trashed or pruned since,
    and handing mpv a path to nothing is how a satellite comes up stuck.
    """
    return [
        (video, funscript)
        for video, funscript in read_playlist(playlist_file)
        if video.exists()
    ]


def _rotate_onto(entries: PlaylistEntries, last_video: str) -> PlaylistEntries:
    """*entries* rotated so *last_video* leads them.

    Unchanged when that video is not among them — it was deleted since, or the
    player published no status at all.  Last session's queue is the thing worth
    keeping, so it comes back from its top rather than being thrown away.
    """
    key = normalize_path_key(last_video)
    for position, (video, _funscript) in enumerate(entries):
        if normalize_path_key(str(video)) == key:
            return entries[position:] + entries[:position]
    return entries


def resume_playlists(resumptions: Sequence[tuple[Path, str]]) -> bool:
    """Rotate each playlist file onto the video its player last had on screen.

    *resumptions* pairs a playlist file with the video named in that player's
    status file.  Returns whether there was a session to come back to at all: a
    playlist file that is missing, or that has no clip left on disk, means there
    is not — a first run, a wiped state dir — and the caller builds fresh instead.

    All or nothing, because one build writes all three playlists: every rotation
    is worked out before any of them is written, so a session either resumes
    whole or is left exactly as the last build wrote it.
    """
    rotated: list[tuple[Path, PlaylistEntries]] = []
    for playlist_file, last_video in resumptions:
        entries = _surviving_entries(playlist_file)
        if not entries:
            return False
        rotated.append((playlist_file, _rotate_onto(entries, last_video)))
    for playlist_file, entries in rotated:
        write_playlist_entries(playlist_file, entries)
    return True


def resume_satellite_locks(locks: Sequence[tuple[Path, bool]]) -> None:
    """Queue LOCK on the command file of each satellite that was locked.

    *locks* pairs a satellite's command file with whether that side comes back
    locked.  A lock is repeat-one in mpv's own ``loop_file``, which lives in a
    player process that has just been replaced, so unlike a filter or a loop it
    cannot ride back in on a file the new player reads — it has to be re-sent.

    Queued before the satellites launch, so each drains it on its very first
    tick, by which time its session has already loaded the clip the resume put at
    the top of its playlist: the same clip the lock was on when the session
    closed, locked again before a frame of anything else can play.
    """
    for command_file, locked in locks:
        if locked:
            append_command(Path(command_file), "LOCK")


def resume_main_loop(nau_cmd_file: Path, bounds: tuple[int, int] | None) -> None:
    """Queue SET_LOOP on the main player's command file for the loop it was running.

    The main player's counterpart of :func:`resume_satellite_locks`, and re-sent for
    the same reason: an A/B loop is a range inside one video, held in mpv by a
    player process that has just been replaced, so unlike F-mode or an order it
    cannot ride back in on a file the new player reads.  *bounds* is None when
    there was no loop — Nau then simply plays the video through, which is
    already what no loop means.

    Queued before Nau launches, so it drains on the first pass of its command
    file, over the video the resume put at the top of its playlist: the same
    video the loop was cut from.  Nau holds the seek until mpv has the file open
    (see its ``restore_loop``), so it lands however slowly the file opens.
    """
    if bounds is not None:
        append_command(Path(nau_cmd_file), f"{SET_LOOP_CMD} {bounds[0]} {bounds[1]}")


def resume_shared_state(state_file: Path, *, resumed: bool) -> BridgeState:
    """Seed *state_file* with the state a resumed session comes back in.

    Pass *resumed* as :func:`resume_playlists` reported it: the state carried
    forward is only ever the state that explains the files on disk, so a session
    built fresh — nothing to resume, or a rebuild over the top — opens on
    defaults, and one that kept last session's playlists keeps what shaped them
    and what it was left set to (:data:`RESUMED_FIELDS`).

    Written either way, and returned, since the file is what the dispatch loop
    reads its opening state from: the alternative was deleting it at startup,
    which is exactly how a session came back playing favorites while every HUD
    said F-mode was off — and then answered "F-mode" by reporting it *enabled*
    and changing nothing you could see.  Writing defaults clears a crashed
    session's leftovers just as the delete did.
    """
    previous = read_shared_state(state_file) if resumed else None
    state = (
        BridgeState()
        if previous is None
        else BridgeState(**{field: getattr(previous, field) for field in RESUMED_FIELDS})
    )
    write_shared_state(state_file, state)
    return state
