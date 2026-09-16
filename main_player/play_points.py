"""Where each video was left, so playing it again picks up there."""
from __future__ import annotations

import json
import time
from pathlib import Path

from app_support.file_channel import publish_whole


def play_points_filename(who: str) -> str:
    return f"{who}_play_points.json"


REMEMBERED = 5_000
PLAYED_ON_MS = 2_000
WRITE_EVERY_S = 10.0


def _key(video: Path | str) -> str:
    return str(Path(str(video).strip())).lower()


class PlayPoints:
    def __init__(self, path: Path | None, *, clock=time.monotonic) -> None:
        self._path = path
        self._clock = clock
        self._points = self._read()
        self._watching = ""
        self._at_ms = 0.0
        self._written_at = float("-inf")

    def point_for(self, video: Path | str) -> int:
        """Where to open *video*, or 0 for the top of it."""
        return self._points.get(_key(video), 0)

    def observe(self, video: Path | str, position_ms: float, duration_ms: float) -> None:
        """Say where the playhead is this tick."""
        if duration_ms <= 0:
            return
        key = _key(video)
        if key != self._watching:
            self.leave()
            self._watching, self._at_ms = key, position_ms
            return
        played_on = 0 <= position_ms - self._at_ms < PLAYED_ON_MS
        self._at_ms = position_ms
        if played_on and self._clock() - self._written_at >= WRITE_EVERY_S:
            self._write_point()

    def ended(self) -> None:
        self._at_ms = 0.0
        self.leave()

    def leave(self) -> None:
        """Write down exactly where the video being watched was left."""
        if self._watching:
            self._write_point()
        self._watching, self._at_ms = "", 0.0

    def _write_point(self) -> None:
        self._written_at = self._clock()
        key, point = self._watching, max(0, int(self._at_ms))
        if point:
            if self._points.get(key) == point:
                return
            self._points.pop(key, None)
            self._points[key] = point
            while len(self._points) > REMEMBERED:
                del self._points[next(iter(self._points))]
        elif self._points.pop(key, None) is None:
            return
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
