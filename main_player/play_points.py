"""Where each video was left, so playing it again picks up there."""
from __future__ import annotations

import json
from pathlib import Path

from app_support.file_channel import publish_whole

FILENAME = "main_player_play_points.json"

LEAD_IN_MS = 60_000
TAIL_MS = 60_000
RESOLUTION_MS = 5_000
REMEMBERED = 500
PLAYED_ON_MS = 2_000


def _key(video: Path | str) -> str:
    return str(Path(str(video).strip())).lower()


class PlayPoints:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._points = self._read()
        self._watching = ""
        self._at_ms = 0.0

    def point_for(self, video: Path | str) -> int:
        """Where to open *video*, or 0 for the top of it."""
        return self._points.get(_key(video), 0)

    def observe(self, video: Path | str, position_ms: float, duration_ms: float) -> None:
        """Say where the playhead is this tick; a point is written when it moves."""
        key = _key(video)
        played_on = key == self._watching and 0 <= position_ms - self._at_ms < PLAYED_ON_MS
        self._watching, self._at_ms = key, position_ms
        if not played_on or duration_ms <= 0:
            return
        if position_ms > duration_ms - TAIL_MS:
            self._forget(key)
        elif position_ms >= LEAD_IN_MS:
            self._remember(key, int(position_ms // RESOLUTION_MS) * RESOLUTION_MS)

    def _remember(self, key: str, point: int) -> None:
        if self._points.get(key) == point:
            return
        self._points.pop(key, None)
        self._points[key] = point
        while len(self._points) > REMEMBERED:
            del self._points[next(iter(self._points))]
        self._write()

    def _forget(self, key: str) -> None:
        if self._points.pop(key, None) is not None:
            self._write()

    def _read(self) -> dict[str, int]:
        if self._path is None:
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return {str(key): int(value) for key, value in payload.items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _write(self) -> None:
        if self._path is not None:
            publish_whole(self._path, json.dumps(self._points, indent=1) + "\n")
