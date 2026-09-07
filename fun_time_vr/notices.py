"""The session's announcements, held for the headset to draw: the desktop's two
surfaces for a notice both live in the dashboard process, which a VR session
does not launch."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fun_time.event_log import (
    SOURCE_LANDSCAPE,
    SOURCE_PORTRAIT,
    is_announcement,
    read_events,
)

from .layout import PRIMARY

# Long enough to read; short enough to be gone by the next command.
NOTICE_SECONDS = 8.0
KEPT = 3

# What the desktop's toast lingers -- shorter than the strip, since it sits over
# the picture rather than beside it.
TOAST_SECONDS = 2.2

# How much of the stream the dash can reach back through.
KEPT_RECORDS = 400

# Which screen a notice flashes over: the two satellites have their own, and
# everything else belongs to the primary, the desktop's own fallback.
_SCREENS = {SOURCE_PORTRAIT: SOURCE_PORTRAIT, SOURCE_LANDSCAPE: SOURCE_LANDSCAPE}


def screen_for(source: str) -> str:
    return _SCREENS.get(source, PRIMARY)


@dataclass(frozen=True)
class Notice:  # with its screen and the reader's clock when it arrived
    message: str
    level: int
    screen: str
    seen_at: float


class NoticeBoard:
    """One read of the event log per tick, for everything that shows a notice:
    the console's strip, and one toast per screen.  Both fade on the CALLER's
    clock, so a wall-clock stamp and a monotonic pump are never subtracted."""

    def __init__(self, event_log: Path | str, *, seconds: float = NOTICE_SECONDS,
                 kept: int = KEPT, toast_seconds: float = TOAST_SECONDS,
                 kept_records: int = KEPT_RECORDS) -> None:
        self._path = Path(event_log)
        self._seconds = seconds
        self._kept = kept
        self._toast_seconds = toast_seconds
        self._kept_records = kept_records
        self._lines: list[Notice] = []
        self._toasts: dict[str, Notice] = {}
        self._records: list = []
        _, self._offset = read_events(self._path, 0)

    def pump(self, _stop, now: float) -> None:
        """Take what was written since the last call; drop what has faded."""
        records, self._offset = read_events(self._path, self._offset)
        # The dash filters the whole stream itself, so everything is kept.
        self._records = (self._records + records)[-self._kept_records:]
        for record in records:
            if not is_announcement(record):
                continue
            notice = Notice(record.message, record.level, screen_for(record.source), now)
            self._lines.append(notice)
            self._toasts[notice.screen] = notice  # the newest wins its screen
        self._lines = [line for line in self._lines if now - line.seen_at < self._seconds]
        del self._lines[:-self._kept]
        self._toasts = {
            screen: toast for screen, toast in self._toasts.items()
            if now - toast.seen_at < self._toast_seconds
        }

    @property
    def lines(self) -> tuple[Notice, ...]:  # oldest first
        return tuple(self._lines)

    def toast(self, screen: str) -> Notice | None:  # what is flashing over it
        return self._toasts.get(screen)

    @property
    def records(self) -> tuple:  # the whole stream, unfiltered, oldest first
        return tuple(self._records)
