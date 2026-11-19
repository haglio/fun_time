from __future__ import annotations

from dataclasses import dataclass

from shared_ui.message_shape import INFO, time_to_read_ms

from fun_time.event_log import EventRecord
from fun_time.level_colors import message_kind


@dataclass(frozen=True)
class ShownMessage:
    text: str
    kind: str = INFO
    mark: str = ""

    @classmethod
    def of(cls, record: EventRecord) -> ShownMessage:
        return cls(record.message, message_kind(record.level), record.mark)

    @property
    def time_to_read_ms(self) -> int:
        return time_to_read_ms(self.text)
