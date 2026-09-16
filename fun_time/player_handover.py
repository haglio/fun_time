"""A satellite player handed to the hosted Origenerator, and taken back.

The app writes the player's own playlist file, so the session's list is kept
aside — turned onto the clip on screen — and put back when the side comes home,
where the player opens at its top: exactly where it left off.
"""
from __future__ import annotations

from pathlib import Path

from player_core.file_channel import append_command
from player_core.player_verbs import RELOAD_PLAYLIST
from player_core.playlist import read_playlist, write_playlist

from .bridge_records import SideChannel
from .modes import rotated_onto
from .satellite_control import read_satellite_status


def _kept(side: SideChannel) -> Path:
    """Where *side*'s own list waits while the hosted app has the player."""
    return side.playlist_file.with_name(f"{side.playlist_file.stem}.kept.tsv")


def keep_aside(side: SideChannel) -> None:
    """Keep *side*'s list, turned onto the clip the player is showing.

    A side already holding a kept list never came home, and the list its player
    has now is the hosted app's, not the session's.
    """
    if _kept(side).exists():
        return
    entries = read_playlist(side.playlist_file)
    if not entries:
        return
    video = read_satellite_status(side.status_file).video
    write_playlist(_kept(side), rotated_onto(entries, video))


def hand_back(side: SideChannel) -> bool:
    """Give *side*'s player its own list again; ``False`` with none kept.

    The kept list is spent by it: a stale one would be dealt over whatever the
    session has built for that side since.
    """
    kept = _kept(side)
    entries = read_playlist(kept)
    if not entries:
        return False
    write_playlist(side.playlist_file, entries)
    append_command(side.cmd_file, RELOAD_PLAYLIST)
    kept.unlink(missing_ok=True)
    return True
