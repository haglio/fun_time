"""The other versions of the video on screen, as the library knows them.

The library groups a video with its other qualities (a 540p copy, an upscale);
the Funestra puts whichever one is asked for up in the clip's own place
(:meth:`player_core.playback.Playback.step_version`).  What Kino adds is which
files those are, read off the library's index, and whether the one showing is
the one the playlist named.
"""
from __future__ import annotations

from pathlib import Path

from player_core.playlist import PlaylistItem


class Versions:
    def __init__(self, playback, version_index: dict[Path, list[PlaylistItem]] | None) -> None:
        self._playback = playback
        self._index = version_index or {}

    @property
    def has_other_versions(self) -> bool:
        return self._family() is not None

    @property
    def on_default(self) -> bool:
        return self._playback.showing == self._playback.current_video

    def cycle(self, step: int) -> None:
        family = self._family()
        if family is not None:
            self._playback.step_version(family, step)

    def _family(self) -> list[Path] | None:
        showing = self._playback.showing
        versions = self._index.get(showing)
        if versions is None or len(versions) <= 1:
            return None
        files = [version.path for version in versions]
        return files if showing in files else None
