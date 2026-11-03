from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

SETTLE_S = 0.05
LATE_S = 0.5

EVENT_SYSTEM_CAPTURESTART = 0x0008
EVENT_SYSTEM_CAPTUREEND = 0x0009
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
OBJID_WINDOW = 0
OBJID_CARET = -8
_KEPT = 256


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


_SIGHTINGS = {
    (EVENT_SYSTEM_CAPTURESTART, OBJID_WINDOW): Sighting.PRESS,
    (EVENT_SYSTEM_CAPTUREEND, OBJID_WINDOW): Sighting.RELEASE,
    (EVENT_OBJECT_SHOW, OBJID_CARET): Sighting.CARET_SHOWN,
    (EVENT_OBJECT_HIDE, OBJID_CARET): Sighting.CARET_GONE,
    (EVENT_OBJECT_DESTROY, OBJID_CARET): Sighting.CARET_GONE,
}


class DesktopEvents:
    def __init__(self, *, root_of: Callable[[int], int], process_of: Callable[[int], int],
                 text_pointer: Callable[[], bool],
                 now: Callable[[], float] = time.monotonic) -> None:
        self._root_of = root_of
        self._process_of = process_of
        self._text_pointer = text_pointer
        self._now = now
        seen: deque[Seen] = deque(maxlen=_KEPT)
        self._seen = seen
        self._remember = seen.append
        self._oldest = seen.popleft

    def heard(self, event: int, hwnd: int, id_object: int) -> None:
        sighting = _SIGHTINGS.get((event, id_object))
        if sighting is None or not hwnd:
            return
        window = self._root_of(hwnd) or hwnd
        self._remember(Seen(
            sighting, window=int(window), process=self._process_of(window), at=self._now(),
            with_the_text_pointer=sighting is Sighting.PRESS and self._text_pointer()))

    def take(self) -> list[Seen]:
        taken: list[Seen] = []
        while self._seen:
            taken.append(self._oldest())
        return taken


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


class TypingWatch:
    def __init__(self, sightings: Sightings | None, *, typed_into: Callable[[Seen], bool]) -> None:
        self._sightings = sightings
        self._clicks = TextFieldClicks(watched=typed_into)

    def start(self) -> None:
        if self._sightings is not None:
            self._sightings.start()

    def stop(self) -> None:
        if self._sightings is not None:
            self._sightings.stop()

    def clicked_into_a_text_field(self, *, now: float) -> bool:
        if self._sightings is None:
            return False
        for seen in self._sightings.take():
            self._clicks.saw(seen)
        return self._clicks.clicked_into(now=now) is not None
