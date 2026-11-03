from __future__ import annotations

from pathlib import Path

from funestra.playlist import PlaylistItem


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
