from __future__ import annotations

import logging
from pathlib import Path

from funestra.modes import MessageLevel
from funestra.playback import funscripts_of
from funestra.playlist import PlaylistItem

logger = logging.getLogger(__name__)


class ClipJumps:
    def __init__(self, nav, playback, funscripts: dict[Path, Path | None], messages) -> None:
        self._nav = nav
        self._playback = playback
        self._funscripts = funscripts
        self._messages = messages
        self._compilation = ""
        self._video_the_answers_are_for: Path | None = None
        self._answers: tuple[bool, str, str] = (False, "", "")

    @property
    def compilation(self) -> str:
        return self._compilation

    @property
    def has_compilation(self) -> bool:
        return self._answers_for_the_video_on_screen()[0]

    @property
    def jump_to(self) -> str:
        return self._answers_for_the_video_on_screen()[1]

    @property
    def title(self) -> str:
        return self._answers_for_the_video_on_screen()[2]

    def _answers_for_the_video_on_screen(self) -> tuple[bool, str, str]:
        current = self._playback.current_video
        if current != self._video_the_answers_are_for:
            self._video_the_answers_are_for = current
            self._answers = self._walk_the_library_for(current)
        return self._answers

    def _walk_the_library_for(self, video: Path) -> tuple[bool, str, str]:
        return (
            bool(self._nav.compilation_of(video)),
            "scene" if self._nav.full_vid_of(video) is not None
            else "clip" if self._nav.clip_of(video) is not None else "",
            self._nav.title_of(video),
        )

    def _item(self, video: Path) -> PlaylistItem:
        return PlaylistItem(video, self._funscripts.get(video))

    def _swap_the_list_in_memory(self, items: list[PlaylistItem]) -> None:
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
        self._swap_the_list_in_memory(playlist)

    def play_compilation(self) -> None:
        current = self._playback.current_video
        if not self._enter(current):
            self._messages.say("not a compilation clip", level=MessageLevel.WARNING)
            return
        self._messages.say(
            f"compilation: {len(self._playback.playlist)} clips", level=MessageLevel.NORMAL)

    def _enter(self, current: Path) -> bool:
        siblings = self._nav.compilation_playlist(current)
        if not siblings:
            return False
        self._swap_the_list_in_memory([self._item(video) for video in siblings])
        self._compilation = self._nav.compilation_of(current)
        return True

    def play_full_vid(self) -> None:
        self._jump(self._nav.full_vid_of(self._playback.current_video), "full video")

    def play_clip_jump(self) -> None:
        self._jump(self._nav.clip_of(self._playback.current_video), "clip jump")

    def _jump(self, target: Path | None, what: str) -> None:
        if target is None:
            logger.info("%s: nothing matches %s", what, self._playback.current_video.name)
            self._messages.say(f"{what} not available", level=MessageLevel.WARNING)
            return
        self._playback.play_file(target, self._funscripts.get(target))
        self._messages.say(what, level=MessageLevel.NORMAL)
