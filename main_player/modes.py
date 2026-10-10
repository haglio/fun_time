"""The modes Kino is playing in, and what changes them.

Three of them, and they are not the same kind of thing.  The *length mode* is
the library's own filter -- mixed, clips, full -- and changing it rebuilds the
playlist.  The *compilation* is one anthology's clips standing in for the
playlist, which :mod:`main_player.clip_jumps` owns because entering one is what puts you
there.  *F-mode* is Fun Time's filter over whichever of those is running, and
Kino cannot see it: the narrowed playlist it receives is indistinguishable from
any other, so the flag has to be said outright for the console to show it.

They are gathered here because the console draws them as one line and the mode
memory writes them down as one record.
"""
from __future__ import annotations

import logging

from player_core.console_hud import ModeHud
from player_core.modes import LengthMode, read_mode
from player_core.playback import funscripts_of

from .library_source import DEFAULT_MODE, length_mode_rebuilds, next_length_mode
from .mode_memory import RememberedMode
from .status import LibraryStatus

logger = logging.getLogger(__name__)


def reload_playlist(playback, jumps, resolve) -> None:
    items = resolve()
    if items:
        playback.replace_playlist([item.path for item in items], funscripts_of(items))
    jumps.leave_compilation()


class Modes:
    def __init__(self, source, playback, jumps, versions, *, remembered: str) -> None:
        self._source = source
        self._playback = playback
        self._jumps = jumps
        self.versions = versions
        self._length_mode = (remembered or DEFAULT_MODE) if source is not None else None
        self._scripted_filter = False

    @property
    def length_mode(self) -> LengthMode | None:
        return self._length_mode

    @property
    def scripted_filter(self) -> bool:
        return self._scripted_filter

    def set_scripted_filter(self, on: bool) -> None:
        self._scripted_filter = on

    def set_length(self, mode: str) -> None:
        if self._source is None:
            return
        mode = read_mode(LengthMode, mode.strip().lower(), None)
        if mode is None:
            return
        if not length_mode_rebuilds(mode, self.length_mode,
                                    in_compilation=bool(self._jumps.compilation)):
            return
        self._length_mode = mode
        self._jumps.leave_compilation()
        logger.info("Length mode: %s", mode)
        if mode is LengthMode.NONE:
            self._playback.set_locked(True)
            return
        items = self._source.playlist_for(mode)
        if items:
            self._playback.load_playlist([item.path for item in items], funscripts_of(items))

    def toggle_length(self) -> None:
        self.set_length(next_length_mode(self.length_mode))

    def end_compilation(self) -> None:
        if self._source is None:
            return
        self._jumps.end_compilation(self._source.playlist_for(self.length_mode))

    @property
    def hud(self) -> ModeHud:
        return ModeHud(
            video=self._name_on_screen,
            length_mode=self.length_mode,
            compilation=self._jumps.compilation,
            position=self._playback.index + 1,
            total=len(self._playback.playlist),
            scripted_filter=self.scripted_filter,
        )

    @property
    def _name_on_screen(self) -> str:
        if not self._playback.switching_versions and self.versions.on_default:
            return self._jumps.title
        return f"{self._jumps.title} ({self._playback.showing.name})"

    @property
    def library_status(self) -> LibraryStatus:
        return LibraryStatus(
            length_mode=self.length_mode,
            compilation=self._jumps.compilation,
            has_compilation=self._jumps.has_compilation,
            has_other_versions=self.versions.has_other_versions,
            jump_to=self._jumps.jump_to,
        )

    @property
    def remembered(self) -> RememberedMode:
        return RememberedMode(
            length_mode=self.length_mode,
            compilation=self._jumps.compilation,
            video=str(self._playback.current_video) if self._jumps.compilation else "",
        )
