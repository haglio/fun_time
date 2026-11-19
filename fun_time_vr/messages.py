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
from fun_time.shown_message import ShownMessage
from fun_time.unlogged_messages import UnloggedMessages

from .layout import LANDSCAPE, MAIN, PORTRAIT

KEPT_RECORDS = 400

_SCREENS = {SOURCE_PORTRAIT: PORTRAIT, SOURCE_LANDSCAPE: LANDSCAPE}


def screen_for(source: str) -> str:
    return _SCREENS.get(source, MAIN)


@dataclass(frozen=True)
class FlashedMessage:
    shown: ShownMessage
    screen: str
    seen_at_on_the_pumps_clock: float

    def still_up_at(self, now: float) -> bool:
        return now - self.seen_at_on_the_pumps_clock < self.shown.time_to_read_ms / 1000


class MessageBoard:
    def __init__(self, event_log: Path | str, *, unlogged: UnloggedMessages | None = None,
                 kept_records: int = KEPT_RECORDS) -> None:
        self._path = Path(event_log)
        self._unlogged = unlogged
        self._kept_records = kept_records
        self._flashed_messages: dict[str, FlashedMessage] = {}
        self._lock = threading.Lock()
        self._records: list = []
        self._heard: dict[float, str] = {}
        _, self._offset = read_events(self._path, 0)

    def pump(self, _stop, now: float) -> None:
        records, self._offset = read_events(self._path, self._offset)
        self._records = (self._records + records)[-self._kept_records:]
        unlogged = self._unlogged.take_all() if self._unlogged is not None else []
        self._heard = remember_what_was_heard(self._heard, unlogged, keep=self._kept_records)
        with self._lock:
            for record in sorted([*filter(is_announcement, records), *unlogged],
                                 key=lambda record: record.ts):
                flashed = FlashedMessage(ShownMessage.of(record), screen_for(record.source), now)
                self._flashed_messages[flashed.screen] = flashed
            self._flashed_messages = {
                screen: flashed for screen, flashed in self._flashed_messages.items()
                if flashed.still_up_at(now)
            }

    def flash(self, shown: ShownMessage, *, screen: str, now: float) -> None:
        with self._lock:
            self._flashed_messages[screen] = FlashedMessage(shown, screen, now)

    def flashed(self, screen: str) -> FlashedMessage | None:
        with self._lock:
            return self._flashed_messages.get(screen)

    @property
    def records(self) -> tuple:
        return tuple(with_what_was_heard(self._records, self._heard))

    def close(self) -> None:
        if self._unlogged is not None:
            self._unlogged.stop()
