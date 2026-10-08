from __future__ import annotations

import time
from pathlib import Path

from fun_time.event_log import EventRecord, event_line, parse_event_line
from fun_time.loopback_inbox import LoopbackInbox, post_to_inbox

UNLOGGED_NOTICE_PORT_FILENAME = "unlogged_notice_port.txt"


def flash_unlogged(state_dir: Path, message: str, *, source: str, level: int,
                   spoken_at: float | None = None) -> bool:
    return post_to_inbox(state_dir / UNLOGGED_NOTICE_PORT_FILENAME,
                         event_line(EventRecord(time.time(), level, source, message,
                                                spoken_at=spoken_at)))


class UnloggedNotices:
    def __init__(self, state_dir: Path) -> None:
        self._inbox = LoopbackInbox(state_dir / UNLOGGED_NOTICE_PORT_FILENAME,
                                    listener_name="unlogged-notice-listener")

    def take_all(self) -> list[EventRecord]:
        return [record for record in map(parse_event_line, self._inbox.take_all())
                if record is not None]

    def stop(self) -> None:
        self._inbox.stop()
