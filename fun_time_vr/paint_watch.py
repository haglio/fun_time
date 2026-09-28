from __future__ import annotations

FLUSH_AFTER_S = 1.0
GIVE_UP_AFTER_S = 5.0
NO_PICTURE_AFTER_S = 6.0
READ_EVERY_S = 1.0

WAIT = "wait"
FLUSH = "flush"
DONE = "done"
GIVE_UP = "give up"


class GpuWait:
    def __init__(self) -> None:
        self._since: float | None = None
        self._flushed = False

    def judge(self, *, reached: bool, now: float) -> str:
        if reached:
            self._since, self._flushed = None, False
            return DONE
        if self._since is None:
            self._since = now
            return WAIT
        waited = now - self._since
        if waited >= GIVE_UP_AFTER_S:
            self._since, self._flushed = None, False
            return GIVE_UP
        if waited >= FLUSH_AFTER_S and not self._flushed:
            self._flushed = True
            return FLUSH
        return WAIT


class PictureWatch:
    def __init__(self) -> None:
        self._gap: tuple[float, float] | None = None
        self._read_at: float | None = None
        self._said = False

    def drew(self) -> None:
        self._gap, self._read_at, self._said = None, None, False

    def wants_a_reading(self, now: float) -> bool:
        return self._read_at is None or now - self._read_at >= READ_EVERY_S

    def read(self, *, position_ms: float, now: float) -> float | None:
        self._read_at = now
        if self._gap is None:
            self._gap = (now, position_ms)
            return None
        since, first_position_ms = self._gap
        gone = now - since
        if position_ms == first_position_ms or gone < NO_PICTURE_AFTER_S or self._said:
            return None
        self._said = True
        return gone
