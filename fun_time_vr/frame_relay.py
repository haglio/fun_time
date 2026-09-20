from __future__ import annotations

import threading
from dataclasses import dataclass

SLOTS = 3


class StillAsked:
    """A one-frame copy asked for by another thread, painted by the one that owns
    the GL context -- the picture check's way in, since mpv's own screenshot needs
    that context and refuses from anywhere else."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wanted = False
        self._left = None

    @property
    def wanted(self) -> bool:
        with self._lock:
            return self._wanted

    def ask(self) -> None:
        with self._lock:
            if self._left is None:
                self._wanted = True

    def leave(self, still) -> None:
        with self._lock:
            self._wanted = False
            self._left = still

    def take(self):
        with self._lock:
            still, self._left = self._left, None
        return still


def capped_size(dims: tuple[int, int], cap_px: int) -> tuple[int, int] | None:
    width, height = dims
    if not width or not height:
        return None
    scale = min(1.0, cap_px / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


@dataclass(frozen=True)
class Picture:
    slot: int
    texture: int
    width: int
    height: int
    number: int
    video: str


class FrameRelay:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._newest: Picture | None = None
        self._shown = 0
        self._copying: int | None = None

    def slot_to_paint(self) -> int:
        with self._lock:
            spoken_for = {self._copying, None if self._newest is None else self._newest.slot}
        return next(slot for slot in range(SLOTS) if slot not in spoken_for)

    def painted(self, slot: int, *, texture: int, width: int, height: int, video: str) -> None:
        with self._lock:
            number = 1 if self._newest is None else self._newest.number + 1
            self._newest = Picture(slot, texture, width, height, number, video)

    def take(self) -> Picture | None:
        with self._lock:
            newest = self._newest
            if self._copying is not None or newest is None or newest.number == self._shown:
                return None
            self._shown = newest.number
            self._copying = newest.slot
            return newest

    def copied(self) -> None:
        with self._lock:
            self._copying = None
