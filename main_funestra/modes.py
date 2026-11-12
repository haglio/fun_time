from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from funestra.main_hud import ModeHud
from funestra.playback import funscripts_of

from .gallery_names import NO_GALLERY_NAMES
from .mode_memory import RememberedMode
from .status import LibraryStatus


def reload_playlist(playback, jumps, resolve) -> None:
    items = resolve()
    if items:
        playback.replace_playlist([item.path for item in items], funscripts_of(items))
    jumps.leave_compilation()


class Modes:
    def __init__(self, playback, jumps, versions, *, the_main_list,
                 gallery_name_of: Callable[[Path], str] = NO_GALLERY_NAMES.of) -> None:
        self._playback = playback
        self._gallery_name_of = gallery_name_of
        self._jumps = jumps
        self.versions = versions
        self._the_main_list = the_main_list
        self._scripted_filter = False

    @property
    def scripted_filter(self) -> bool:
        return self._scripted_filter

    def set_scripted_filter(self, on: bool) -> None:
        self._scripted_filter = on

    def end_compilation(self) -> None:
        self._jumps.end_compilation(self._the_main_list())

    @property
    def hud(self) -> ModeHud:
        return ModeHud(
            video=self._name_on_screen,
            compilation=self._jumps.compilation,
            position=self._playback.index + 1,
            total=len(self._playback.playlist),
            scripted_filter=self.scripted_filter,
        )

    @property
    def _name_on_screen(self) -> str:
        name = self._gallery_name_of(self._playback.current_video) or self._jumps.title
        if not self._playback.switching_versions and self.versions.on_default:
            return name
        return f"{name} ({self._playback.showing.name})"

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
