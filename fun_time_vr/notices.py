"""The session's announcements, held for the headset to draw: the desktop's two
surfaces for a notice both live in the dashboard process, which a VR session
does not launch."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from fun_time.event_log import (
    SOURCE_LANDSCAPE,
    SOURCE_PORTRAIT,
    is_announcement,
    read_events,
)
from fun_time.unlogged_notices import UnloggedNotices

from .layout import MAIN

BANNER_SECONDS = 2.2

# How much of the stream the dash can reach back through.
KEPT_RECORDS = 400

# Which screen a notice flashes over: the two satellites have their own, and
# everything else is the main screen's, the desktop's own fallback.
_SCREENS = {SOURCE_PORTRAIT: SOURCE_PORTRAIT, SOURCE_LANDSCAPE: SOURCE_LANDSCAPE}


def screen_for(source: str) -> str:
    return _SCREENS.get(source, MAIN)


@dataclass(frozen=True)
class Notice:  # with its screen and the reader's clock when it arrived
    message: str
    level: int
    screen: str
    seen_at: float


class NoticeBoard:
    """One read of the event log per tick, for the two things that show a notice:
    one banner per screen, and the dash's own stream.  A banner fades on the
    CALLER's clock, so a wall-clock stamp and a monotonic pump are never
    subtracted."""

    def __init__(self, event_log: Path | str, *, unlogged: UnloggedNotices | None = None,
                 banner_seconds: float = BANNER_SECONDS,
                 kept_records: int = KEPT_RECORDS) -> None:
        self._path = Path(event_log)
        self._unlogged = unlogged
        self._banner_seconds = banner_seconds
        self._kept_records = kept_records
        self._banners: dict[str, Notice] = {}
        self._lock = threading.Lock()
        self._records: list = []
        _, self._offset = read_events(self._path, 0)

    def pump(self, _stop, now: float) -> None:  # take the new, drop the faded
        records, self._offset = read_events(self._path, self._offset)
        # The dash filters the whole stream itself, so everything is kept.
        self._records = (self._records + records)[-self._kept_records:]
        unlogged = self._unlogged.take_all() if self._unlogged is not None else []
        with self._lock:
            for record in sorted([*filter(is_announcement, records), *unlogged],
                                 key=lambda record: record.ts):
                notice = Notice(record.message, record.level, screen_for(record.source), now)
                self._banners[notice.screen] = notice  # the newest wins its screen
            self._banners = {
                screen: banner for screen, banner in self._banners.items()
                if now - banner.seen_at < self._banner_seconds
            }

    def flash(self, message: str, *, level: int, screen: str, now: float) -> None:
        with self._lock:
            self._banners[screen] = Notice(message, level, screen, now)

    def banner(self, screen: str) -> Notice | None:  # what is flashing over it
        with self._lock:
            return self._banners.get(screen)

    @property
    def records(self) -> tuple:  # the whole stream, unfiltered, oldest first
        return tuple(self._records)

    def close(self) -> None:
        if self._unlogged is not None:
            self._unlogged.stop()
