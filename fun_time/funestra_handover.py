"""A Satellite Funestra handed to the hosted Origenerator, and taken back.

The app writes the Funestra's own playlist file, so the session's list is kept
aside — turned onto the clip on screen — and put back when the Funestra comes home,
where the Funestra opens at its top: exactly where it left off.
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING

from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_OFF, LOCK_ON, RELOAD_PLAYLIST
from funestra.playlist import read_playlist, write_playlist
from funestra.satellite_hud import parse_hud

from .bridge_records import BridgeConfig, SatelliteChannel
from .modes import rotated_onto
from .origenerator_switches import origenerator_shows
from .room_funestras import RoomFunestra
from .satellite_control import read_satellite_status

if TYPE_CHECKING:
    from .shared_state import BridgeState

HudStamp = tuple[int, int] | None


def hud_stamp(channel: SatelliteChannel) -> HudStamp:
    """Which copy of *channel*'s hosted HUD is on disk; each publish replaces it."""
    if channel.origenerator_hud_file is None:
        return None
    try:
        stat = channel.origenerator_hud_file.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns


def show_f_mode(channel: SatelliteChannel) -> bool | None:
    if channel.origenerator_hud_file is None:
        return None
    try:
        hud = parse_hud(channel.origenerator_hud_file.read_text(encoding="utf-8"))
    except OSError:
        return None
    if hud is None:
        return None
    return next((button.lit for row in hud.rows for button in row
                 if button.command == f"{hud.funestra}_fmode"), None)


def let_go_since(channel: SatelliteChannel, stamp: HudStamp) -> bool:
    """Whether the hosted app has published *channel*'s HUD empty since *stamp* --
    its close, which only reading the way out makes it do.  An empty one already
    there at *stamp* is an app that may not have taken the Funestra yet."""
    if hud_stamp(channel) == stamp:
        return False
    try:
        return not channel.origenerator_hud_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _kept(playlist_file: Path) -> Path:
    """Where a Funestra's own list waits while the hosted app has the Funestra."""
    return playlist_file.with_name(f"{playlist_file.stem}.kept.tsv")


def keep_aside(channel: SatelliteChannel) -> None:
    """Keep *channel*'s list, turned onto the clip the Funestra is showing.

    A Funestra already holding a kept list never came home, and the list it
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
    session has built for that Funestra since.
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


# How long the hosted app is given to let go of the Funestras when origenerator
# mode ends: a ceiling for an app that has stalled, not a wait anyone sits through.
LET_GO_TIMEOUT_S = 3.0


class Homecoming:
    """Satellites on their way back from the hosted app: by when each lands,
    and which of its hosted panels was up when it was sent for."""

    def __init__(self) -> None:
        self._coming: dict[RoomFunestra, tuple[float, HudStamp]] = {}

    @property
    def funestras(self) -> Collection[RoomFunestra]:
        return self._coming.keys()

    def expect(self, funestras: tuple[RoomFunestra, ...], config: BridgeConfig, *,
               now: float) -> None:
        for funestra in funestras:
            self._coming[funestra] = (
                now + LET_GO_TIMEOUT_S, hud_stamp(config.satellite(funestra)))

    def bring(self, state: BridgeState, config: BridgeConfig, *, now: float) -> None:
        """Hand each satellite its own list again once the hosted app has let
        go of it, or has had long enough to -- never before, when a list the app
        wrote after it would take the Funestra straight back."""
        for funestra, (deadline, stamp) in list(self._coming.items()):
            if origenerator_shows(state.satellite(funestra).mode):
                del self._coming[funestra]
                continue
            channel = config.satellite(funestra)
            if now < deadline and not let_go_since(channel, stamp):
                continue
            del self._coming[funestra]
            hand_back(channel)
            # The session's own hold stands again, whatever the app left.
            append_command(channel.cmd_file,
                           LOCK_ON if state.satellite(funestra).locked else LOCK_OFF)
