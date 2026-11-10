# mpv's clock follows audio: an output device that stops draining freezes its video on the frame it
# held.
from __future__ import annotations

LONGER_THAN_A_SLOW_OPEN_S = 6.0

STALLED = "stalled"
STILL_STALLED = "still stalled"


class PlaybackWatch:
    def __init__(self, *, seconds: float = LONGER_THAN_A_SLOW_OPEN_S) -> None:
        self._seconds = seconds
        self._position: float | None = None
        self._since = 0.0
        self._said = ""

    def note(self, *, position_ms: float, playing: bool, now: float) -> str | None:
        held_on_purpose_or_still_opening = not playing
        if held_on_purpose_or_still_opening:
            self._position, self._said = None, ""
            return None
        if position_ms != self._position:
            self._position, self._since, self._said = position_ms, now, ""
            return None
        if now - self._since < self._seconds or self._said == STILL_STALLED:
            return None
        self._give_the_callers_reopen_a_full_window_from(now)
        self._said = STALLED if not self._said else STILL_STALLED
        return self._said

    def _give_the_callers_reopen_a_full_window_from(self, now: float) -> None:
        self._since = now
