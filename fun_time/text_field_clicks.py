from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

SETTLE_S = 0.05
LATE_S = 0.5


class Sighting(Enum):
    PRESS = auto()
    RELEASE = auto()
    CARET_SHOWN = auto()
    CARET_GONE = auto()


@dataclass(frozen=True)
class Seen:
    sighting: Sighting
    window: int
    process: int
    at: float
    with_the_text_pointer: bool = False


class Sightings(Protocol):
    def start(self) -> None: ...

    def take(self) -> list[Seen]: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class _Caret:
    showing: bool
    since: float


_NO_CARET_YET = _Caret(showing=False, since=0.0)


class TextFieldClicks:
    def __init__(self, *, watched: Callable[[Seen], bool]) -> None:
        self._watched = watched
        self._press: Seen | None = None
        self._released_at: float | None = None
        self._carets: dict[int, _Caret] = {}

    def saw(self, seen: Seen) -> None:
        if seen.sighting is Sighting.PRESS:
            self._press = seen if seen.with_the_text_pointer and self._watched(seen) else None
            self._released_at = None
        elif seen.sighting is Sighting.RELEASE:
            if self._press is not None and seen.window == self._press.window:
                self._released_at = seen.at
        else:
            self._carets[seen.window] = _Caret(
                showing=seen.sighting is Sighting.CARET_SHOWN, since=seen.at)

    def clicked_into(self, *, now: float) -> int | None:
        if self._press is None or self._released_at is None:
            return None
        window = self._press.window
        caret = self._carets.get(window, _NO_CARET_YET)
        if now < max(self._released_at, caret.since) + SETTLE_S:
            return None
        if caret.showing:
            self._press = None
            return window
        if now >= self._released_at + LATE_S:
            self._press = None
        return None
