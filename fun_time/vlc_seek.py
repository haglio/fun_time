"""Rapid-nudge seek accumulation for the Primary VLC."""
from __future__ import annotations

import time
from typing import Callable

NUDGE_SECONDS = 10
# How long a commanded seek target stays "hot".  While hot, further nudges
# accumulate onto our own running target instead of re-reading VLC's reported
# position — which lags behind (and regresses during) in-flight seeks, so
# re-reading mid-burst is exactly what makes rapid presses fail to stack.
TARGET_TTL_S = 2.0


class PrimarySeekAccumulator:
    """Turns rapid relative nudges into stacked absolute seeks.

    VLC computes a relative HTTP seek against its current input time, which it
    only refreshes a few times per second and which regresses while a seek is
    buffering.  Presses arriving faster than that all read the same stale base,
    so N rapid ``+10`` seeks land near +10 instead of +N*10 — and the reported
    position visibly bounces backward as VLC's periodic update fights the
    in-flight seeks.

    This accumulator keeps its own running target: the first nudge of a burst
    reads VLC's real position, and every nudge within ``TARGET_TTL_S`` adds onto
    the last commanded target and issues one absolute seek.  The final absolute
    seek always wins, so a burst of N presses lands exactly N*10s away.
    """

    def __init__(
        self,
        *,
        read_position: Callable[[], tuple[float, float] | None],
        seek: Callable[[float], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read_position = read_position
        self._seek = seek
        self._clock = clock
        self._target: float | None = None
        self._length: float | None = None
        self._deadline = 0.0

    def nudge(self, steps: int) -> None:
        """Seek by *steps* signed 10-second increments (e.g. +1 forward, -1 back)."""
        now = self._clock()
        if self._target is None or now > self._deadline:
            pos = self._read_position()
            if pos is None:
                return
            base, length = pos
            # VLC reports 0 for unknown/unprobed length — treat as no ceiling.
            self._length = length if length and length > 0 else None
        else:
            base = self._target
        target = max(0.0, base + steps * NUDGE_SECONDS)
        if self._length is not None:
            target = min(target, self._length)
        self._target = target
        self._deadline = now + TARGET_TTL_S
        self._seek(target)

    def invalidate(self) -> None:
        """Drop the running target — call when the primary video changes."""
        self._target = None
