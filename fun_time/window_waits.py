from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import NamedTuple

logger = logging.getLogger(__name__)

A_FRAME_S = 1.0 / 60.0
LONG_S = 0.1
QUIET_S = 1.0
SUM_UP_EVERY_S = 60.0


class _Wait(NamedTuple):
    took: float = 0.0
    what: str = ""


class WindowWaits:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        now = clock()
        self._said_at = now - QUIET_S
        self._summed_at = now
        self._unsaid_slow = 0
        self._worst_unsaid = _Wait()
        self._calls = self._slow = self._long = self._given_up = 0
        self._waited = 0.0
        self._worst = _Wait()

    def waited(self, what: str, took: float, *, answered: bool) -> None:
        now = self._clock()
        self._count(_Wait(took, what), answered=answered)
        if self._unsaid_slow and now - self._said_at >= QUIET_S:
            self._say(now)
        if now - self._summed_at >= SUM_UP_EVERY_S:
            self._sum_up(now)

    def _count(self, this: _Wait, *, answered: bool) -> None:
        self._calls += 1
        self._waited += this.took
        self._given_up += not answered
        self._long += this.took > LONG_S
        if this.took > self._worst.took:
            self._worst = this
        if this.took > A_FRAME_S:
            self._slow += 1
            self._unsaid_slow += 1
            if this.took > self._worst_unsaid.took:
                self._worst_unsaid = this

    def _say(self, now: float) -> None:
        logger.info("Slow window call: %s waited %.1f ms; %d slow call(s) since the last line",
                    self._worst_unsaid.what, self._worst_unsaid.took * 1000.0,
                    self._unsaid_slow)
        self._said_at = now
        self._unsaid_slow = 0
        self._worst_unsaid = _Wait()

    def _sum_up(self, now: float) -> None:
        logger.info(
            "Window calls over %.0f s: %d to other programs' windows, %d over %.1f ms, "
            "%d over %.0f ms, %d given up on, %.1f ms waited in all, worst %.1f ms (%s)",
            now - self._summed_at, self._calls, self._slow, A_FRAME_S * 1000.0,
            self._long, LONG_S * 1000.0, self._given_up, self._waited * 1000.0,
            self._worst.took * 1000.0, self._worst.what)
        self._summed_at = now
        self._calls = self._slow = self._long = self._given_up = 0
        self._waited = 0.0
        self._worst = _Wait()
