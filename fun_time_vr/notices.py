"""The session's announcements, held for the headset to draw: the desktop's two
surfaces for a notice both live in the dashboard process, which a VR session
does not launch."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fun_time.event_log import is_announcement, read_events

# Long enough to read a phrase after looking up, short enough that the strip is
# empty again by the next command; three lines is what a glance wants.
NOTICE_SECONDS = 8.0
KEPT = 3


@dataclass(frozen=True)
class Notice:
    """One announcement and the reader's clock when it arrived."""

    message: str
    level: int
    seen_at: float


class NoticeStrip:
    """Tails the event log, fading each line on the CALLER's clock."""

    def __init__(self, event_log: Path | str, *, seconds: float = NOTICE_SECONDS,
                 kept: int = KEPT) -> None:
        self._path = Path(event_log)
        self._seconds = seconds
        self._kept = kept
        self._lines: list[Notice] = []
        _, self._offset = read_events(self._path, 0)

    def pump(self, now: float) -> None:
        """Take what was written since the last call; drop what has faded."""
        records, self._offset = read_events(self._path, self._offset)
        for record in records:
            if is_announcement(record):
                self._lines.append(Notice(record.message, record.level, now))
        self._lines = [line for line in self._lines if now - line.seen_at < self._seconds]
        del self._lines[:-self._kept]

    @property
    def lines(self) -> tuple[Notice, ...]:  # oldest first
        return tuple(self._lines)

