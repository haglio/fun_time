from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from shared_ui.message_shape import mark_of_a_message_said_now

from fun_time.event_log import (
    EventRecord,
    event_line,
    parse_event_line,
)
from fun_time.loopback_inbox import LoopbackInbox, post_to_inbox

UNLOGGED_MESSAGE_PORT_FILENAME = "unlogged_notice_port.txt"


def flash_unlogged(state_dir: Path, message: str, *, source: str, level: int,
                   spoken_at: float | None = None) -> bool:
    return post_to_inbox(state_dir / UNLOGGED_MESSAGE_PORT_FILENAME,
                         event_line(EventRecord(time.time(), level, source, message,
                                                spoken_at=spoken_at,
                                                mark=mark_of_a_message_said_now())))


WAKE_WORD = "a message was logged"


def wake_the_dashboard(state_dir: Path) -> bool:
    return post_to_inbox(state_dir / UNLOGGED_MESSAGE_PORT_FILENAME, WAKE_WORD)


class UnloggedMessages:
    def __init__(self, state_dir: Path, *, on_arrival: Callable[[], None] = lambda: None) -> None:
        self._inbox = LoopbackInbox(state_dir / UNLOGGED_MESSAGE_PORT_FILENAME,
                                    listener_name="unlogged-notice-listener",
                                    on_arrival=on_arrival)

    def take_all(self) -> list[EventRecord]:
        return [record for record in map(parse_event_line, self._inbox.take_all())
                if record is not None]

    def stop(self) -> None:
        self._inbox.stop()
