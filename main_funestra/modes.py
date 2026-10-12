"""The modes Kino is playing in, and what changes them.

The *compilation* is one anthology's clips standing in for the playlist, which
:mod:`main_funestra.clip_jumps` owns because entering one is what puts you
there.  The length and *F-mode* are Fun Time's narrowing of the Main player's
one list, and Kino cannot see either: the narrowed list it receives is
indistinguishable from any other, so each has to be said outright for the
console to show it.

They are gathered here because the console draws them as one line and the mode
memory writes the compilation down.
"""
from __future__ import annotations

from funestra.console_hud import ModeHud
from funestra.modes import LengthMode, read_mode
from funestra.playback import funscripts_of

from .mode_memory import RememberedMode
from .status import LibraryStatus


def reload_playlist(playback, jumps, resolve) -> None:
    items = resolve()
    if items:
        playback.replace_playlist([item.path for item in items], funscripts_of(items))
    jumps.leave_compilation()


class Modes:
    def __init__(self, playback, jumps, versions, *, the_main_list) -> None:
        self._playback = playback
        self._jumps = jumps
        self.versions = versions
        self._the_main_list = the_main_list
        self._length_mode: LengthMode | None = None
        self._scripted_filter = False

    @property
    def length_mode(self) -> LengthMode | None:
        return self._length_mode

    @property
    def scripted_filter(self) -> bool:
        return self._scripted_filter

    def set_scripted_filter(self, on: bool) -> None:
        self._scripted_filter = on

    def set_length(self, said: str) -> None:
        self._length_mode = read_mode(LengthMode, said.strip().lower(), self._length_mode)

    def end_compilation(self) -> None:
        self._jumps.end_compilation(self._the_main_list())

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
            compilation=self._jumps.compilation,
            has_compilation=self._jumps.has_compilation,
            has_other_versions=self.versions.has_other_versions,
            jump_to=self._jumps.jump_to,
        )

    @property
    def remembered(self) -> RememberedMode:
        return RememberedMode(
            compilation=self._jumps.compilation,
            video=str(self._playback.current_video) if self._jumps.compilation else "",
        )
