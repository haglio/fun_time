"""The session's announcements, held for the headset to draw: the desktop's two
surfaces for a message both live in the dashboard process, which a VR session
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
from fun_time.log_panel_model import remember_what_was_heard, with_what_was_heard
from fun_time.unlogged_messages import UnloggedMessages

from .layout import MAIN

MESSAGE_SECONDS = 2.2

# How much of the stream the dash can reach back through.
KEPT_RECORDS = 400

# Which screen a message flashes over: the two satellites have their own, and
# everything else is the main screen's, the desktop's own fallback.
_SCREENS = {SOURCE_PORTRAIT: SOURCE_PORTRAIT, SOURCE_LANDSCAPE: SOURCE_LANDSCAPE}


def screen_for(source: str) -> str:
    return _SCREENS.get(source, MAIN)


@dataclass(frozen=True)
class FlashedMessage:  # with its screen and the reader's clock when it arrived
    message: str
    level: int
    screen: str
    seen_at: float


class MessageBoard:
    """One read of the event log per tick, for the two things that show a message:
    one message per screen, and the dash's own stream.  A message fades on the
    CALLER's clock, so a wall-clock stamp and a monotonic pump are never
    subtracted."""

    def __init__(self, event_log: Path | str, *, unlogged: UnloggedMessages | None = None,
                 message_seconds: float = MESSAGE_SECONDS,
                 kept_records: int = KEPT_RECORDS) -> None:
        self._path = Path(event_log)
        self._unlogged = unlogged
        self._banner_seconds = message_seconds
        self._kept_records = kept_records
        self._flashed_messages: dict[str, FlashedMessage] = {}
        self._lock = threading.Lock()
        self._records: list = []
        self._heard: dict[float, str] = {}
        _, self._offset = read_events(self._path, 0)

    def pump(self, _stop, now: float) -> None:  # take the new, drop the faded
        records, self._offset = read_events(self._path, self._offset)
        # The dash filters the whole stream itself, so everything is kept.
        self._records = (self._records + records)[-self._kept_records:]
        unlogged = self._unlogged.take_all() if self._unlogged is not None else []
        self._heard = remember_what_was_heard(self._heard, unlogged, keep=self._kept_records)
        with self._lock:
            for record in sorted([*filter(is_announcement, records), *unlogged],
                                 key=lambda record: record.ts):
                flashed = FlashedMessage(record.message, record.level, screen_for(record.source), now)
                self._flashed_messages[flashed.screen] = flashed  # the newest wins its screen
            self._flashed_messages = {
                screen: flashed for screen, flashed in self._flashed_messages.items()
                if now - flashed.seen_at < self._banner_seconds
            }

    def flash(self, message: str, *, level: int, screen: str, now: float) -> None:
        with self._lock:
            self._flashed_messages[screen] = FlashedMessage(message, level, screen, now)

    def flashed(self, screen: str) -> FlashedMessage | None:  # what is flashing over it
        with self._lock:
            return self._flashed_messages.get(screen)

    @property
    def records(self) -> tuple:  # the whole stream, unfiltered, oldest first
        return tuple(with_what_was_heard(self._records, self._heard))

    def close(self) -> None:
        if self._unlogged is not None:
            self._unlogged.stop()
