"""A satellite player handed to the hosted Origenerator, and taken back.

The app writes the player's own playlist file, so the session's list is kept
aside — turned onto the clip on screen — and put back when the player comes home,
where the player opens at its top: exactly where it left off.
"""
from __future__ import annotations

from pathlib import Path

from player_core.file_channel import append_command
from player_core.player_verbs import RELOAD_PLAYLIST
from player_core.playlist import read_playlist, write_playlist

from .bridge_records import SatelliteChannel
from .modes import rotated_onto
from .satellite_control import read_satellite_status

PanelStamp = tuple[int, int] | None


def panel_stamp(channel: SatelliteChannel) -> PanelStamp:
    """Which copy of *channel*'s hosted panel is on disk; each publish replaces it."""
    if channel.origenerator_hud_file is None:
        return None
    try:
        stat = channel.origenerator_hud_file.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns


def let_go_since(channel: SatelliteChannel, stamp: PanelStamp) -> bool:
    """Whether the hosted app has published *channel*'s panel empty since *stamp* --
    its close, which only reading the way out makes it do.  An empty one already
    there at *stamp* is an app that may not have taken the player yet."""
    if panel_stamp(channel) == stamp:
        return False
    try:
        return not channel.origenerator_hud_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _kept(playlist_file: Path) -> Path:
    """Where a player's own list waits while the hosted app has the player."""
    return playlist_file.with_name(f"{playlist_file.stem}.kept.tsv")


def keep_aside(channel: SatelliteChannel) -> None:
    """Keep *channel*'s list, turned onto the clip the player is showing.

    A player already holding a kept list never came home, and the list it
    has now is the hosted app's, not the session's.
    """
    kept = _kept(channel.playlist_file)
    if kept.exists():
        return
    entries = read_playlist(channel.playlist_file)
    if not entries:
        return
    video = read_satellite_status(channel.status_file).video
    write_playlist(kept, rotated_onto(entries, video))


def take_back_the_list(playlist_file: Path) -> bool:
    """Put the list kept aside for *playlist_file* back in it; ``False`` with none kept.

    The kept list is spent by it: a stale one would be dealt over whatever the
    session has built for that player since.
    """
    kept = _kept(playlist_file)
    entries = read_playlist(kept)
    if not entries:
        return False
    write_playlist(playlist_file, entries)
    kept.unlink(missing_ok=True)
    return True


def hand_back(channel: SatelliteChannel) -> bool:
    if not take_back_the_list(channel.playlist_file):
        return False
    append_command(channel.cmd_file, RELOAD_PLAYLIST)
    return True
