"""Bring a reopened session back to the clip each player was on.

Every player starts at the top of the playlist file fun_time hands it, and
startup used to overwrite all three with a fresh weighted shuffle — so
reopening Fun Time landed on three clips you had never chosen and lost whatever
you were watching.  Resume instead keeps last session's playlists and rotates
each one onto the clip that was on screen: the player's first entry is where
you left off, and because a playlist wraps, the clips that were coming up still
come up in the same order.

Nothing has to be written at shutdown for this.  Each player already publishes
the video it is playing to its status file every tick, so the last tick before
the session ended is the record — one that survives the force-kill that ends a
session, and a crash or a power cut too, where a shutdown hook would not.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from player_core.playlist import read_playlist

from .media_metadata import normalize_path_key
from .modes import source_roots, write_playlist_entries


PlaylistEntries = list[tuple[Path, Path | None]]


def playlist_fits_sources(playlist_file: Path, sources: str) -> bool:
    """Whether every video in *playlist_file* comes from *sources*.

    A playlist is only ever built from the source spec of the session that
    built it, so an entry from outside this session's spec means the file was
    left by a DIFFERENT app sharing this state dir — today FunTimeVR, whose
    primary rotation merges the VR library into this one's.  Resuming that is
    how VR videos reached the desktop app's primary player, which must never
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
