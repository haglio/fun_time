"""Whether the main player has stopped advancing while it should be playing.

It is the one mpv here that keeps an audio track, and mpv's clock follows audio:
an output device that stops draining freezes its VIDEO on the frame it held.
Every other player here runs with audio off instead (see route_audio).
"""
from __future__ import annotations

# Long enough that a slow open is not a stall, short enough that a session does
# not spend a minute on one frame before anything is said.
STALL_SECONDS = 6.0

STALLED = "stalled"
STILL_STALLED = "still stalled"


class PlaybackWatch:
    """One player's progress, judged against the clock the caller pumps with."""

    def __init__(self, *, seconds: float = STALL_SECONDS) -> None:
        self._seconds = seconds
        self._position: float | None = None
        self._since = 0.0
        self._said = ""

    def note(self, *, position_ms: float, playing: bool, now: float) -> str | None:
        """STALLED the first time, STILL_STALLED one window later if the
        caller's recovery did not take, else None -- each said once."""
        if not playing:
            self._position, self._said = None, ""
            return None
        if position_ms != self._position:
            self._position, self._since, self._said = position_ms, now, ""
            return None
        if now - self._since < self._seconds or self._said == STILL_STALLED:
            return None
        # The window restarts either way, so the second verdict follows a full
        # one later — time the caller's reload needs to take hold.
        self._since = now
        self._said = STALLED if not self._said else STILL_STALLED
        return self._said
