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
        funscripts = funscripts or {}
        self.replaced.append([PlaylistItem(path, funscripts.get(path)) for path in playlist])
        self.playlist = list(playlist)
        if self.current_video not in self.playlist:
            self.restarts += 1

    def load_playlist(self, playlist, funscripts=None) -> None:
        self.replace_playlist(playlist, funscripts)
        self.restarts += 1
