from __future__ import annotations

from pathlib import Path

from funestra.playlist import PlaylistItem


class PlaylistMovesWithRestartsCounted:
    def __init__(self, current: Path, playlist: list[Path]) -> None:
        self.current_video = current
        self.playlist = list(playlist)
        self.replaced: list[list[PlaylistItem]] = []
        self.restarts = 0

    def replace_playlist(self, playlist, funscripts=None) -> None:
        self._take(playlist, funscripts)
        if self.current_video not in self.playlist:
            self._restart_on_the_first()

    def load_playlist(self, playlist, funscripts=None) -> None:
        self._take(playlist, funscripts)
        self._restart_on_the_first()

    def _take(self, playlist, funscripts) -> None:
        funscripts = funscripts or {}
        self.replaced.append([PlaylistItem(path, funscripts.get(path)) for path in playlist])
        self.playlist = list(playlist)

    def _restart_on_the_first(self) -> None:
        self.restarts += 1
        self.current_video = self.playlist[0]
