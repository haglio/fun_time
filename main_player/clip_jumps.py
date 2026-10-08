"""The playlist moves a clip's sidecar makes possible.

:mod:`main_player.clip_nav` reads the ``clip`` metadata Evolver records and answers
questions about one video; this drives the Funestra's playlist from those
answers, which is what Fun Time's "compilation" / "full video" / "clip jump"
actually do.

Playing a compilation replaces the whole playlist, so it is a place you can be
*stuck* -- unlike the other two, which move to one video and leave the playlist
alone.  That is the state the console reports, so it lives here, with whatever
puts you in it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from player_core.modes import NoticeLevel
from player_core.playback import funscripts_of
from player_core.playlist import PlaylistItem

logger = logging.getLogger(__name__)


class ClipJumps:
    def __init__(self, nav, playback, funscripts: dict[Path, Path | None], notices) -> None:
        self._nav = nav
        self._playback = playback
        self._funscripts = funscripts
        self._notices = notices
        self._compilation = ""
        self._asked_about: Path | None = None
        self._reachable: tuple[bool, str, str] = (False, "", "")

    @property
    def compilation(self) -> str:
        return self._compilation

    @property
    def has_compilation(self) -> bool:
        return self._reach()[0]

    @property
    def jump_to(self) -> str:
        return self._reach()[1]

    @property
    def title(self) -> str:
        return self._reach()[2]

    def _reach(self) -> tuple[bool, str, str]:
        current = self._playback.current_video
        if current != self._asked_about:
            self._asked_about = current
            self._reachable = (
                bool(self._nav.compilation_of(current)),
                "scene" if self._nav.full_vid_of(current) is not None
                else "clip" if self._nav.clip_of(current) is not None else "",
                self._nav.title_of(current),
            )
        return self._reachable

    def _item(self, video: Path) -> PlaylistItem:
        return PlaylistItem(video, self._funscripts.get(video))

    def _take_up(self, items: list[PlaylistItem]) -> None:
        self._playback.replace_playlist([item.path for item in items], funscripts_of(items))

    def resume(self, compilation: str, video: Path | None) -> None:
        if not compilation or video is None:
            return
        if self._nav.compilation_of(video) != compilation:
            return
        self._playback.play_file(video, self._funscripts.get(video))
        self._enter(video)

    def leave_compilation(self) -> None:
        self._compilation = ""

    def end_compilation(self, playlist: list[PlaylistItem]) -> None:
        if not self._compilation:
            return
        self._compilation = ""
        current = self._playback.current_video
        if current not in {item.path for item in playlist}:
            playlist = [self._item(current), *playlist]
        self._take_up(playlist)

    def play_compilation(self) -> None:
        current = self._playback.current_video
        if not self._enter(current):
            self._notices.say("not a compilation clip", level=NoticeLevel.WARNING)
            return
        self._notices.say(
            f"compilation: {len(self._playback.playlist)} clips", level=NoticeLevel.NOTICE)

    def _enter(self, current: Path) -> bool:
        siblings = self._nav.compilation_playlist(current)
        if not siblings:
            return False
        self._take_up([self._item(video) for video in siblings])
        self._compilation = self._nav.compilation_of(current)
        return True

    def play_full_vid(self) -> None:
        self._jump(self._nav.full_vid_of(self._playback.current_video), "full video")

    def play_clip_jump(self) -> None:
        self._jump(self._nav.clip_of(self._playback.current_video), "clip jump")

    def _jump(self, target: Path | None, what: str) -> None:
        if target is None:
            logger.info("%s: nothing matches %s", what, self._playback.current_video.name)
            self._notices.say(f"{what} not available", level=NoticeLevel.WARNING)
            return
        self._playback.play_file(target, self._funscripts.get(target))
        self._notices.say(what, level=NoticeLevel.NOTICE)
