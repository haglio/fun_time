"""Whether the Main Funestra has stopped advancing while it should be playing.

It is the one mpv here that keeps an audio track, and mpv's clock follows audio:
an output device that stops draining freezes its VIDEO on the frame it held.
Every other Funestra here runs with audio off instead (see route_audio).
"""
from __future__ import annotations

STALL_SECONDS = 6.0

STALLED = "stalled"
STILL_STALLED = "still stalled"


class PlaybackWatch:
    def __init__(self, *, seconds: float = STALL_SECONDS) -> None:
        self._seconds = seconds
        self._position: float | None = None
        self._since = 0.0
        self._said = ""

    def note(self, *, position_ms: float, playing: bool, now: float) -> str | None:
        if not playing:
            self._position, self._said = None, ""
            return None
        if position_ms != self._position:
            self._position, self._since, self._said = position_ms, now, ""
            return None
        if now - self._since < self._seconds or self._said == STILL_STALLED:
            return None
        self._since = now
        self._said = STALLED if not self._said else STILL_STALLED
        return self._said
