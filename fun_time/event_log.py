# Several processes log here, and an open-append-close write holds no Windows lock against the
# others.
from __future__ import annotations

import json
import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path

from shared_ui.message_shape import mark_of_a_message_said_now

MESSAGE = 25
logging.addLevelName(MESSAGE, "NOTICE")

FAVORITE = MESSAGE + 1
logging.addLevelName(FAVORITE, "FAVORITE")

LEVELS_BY_NAME: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "NOTICE": MESSAGE,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
LEVEL_NAMES: tuple[str, ...] = tuple(LEVELS_BY_NAME)
EVERYTHING_THE_DIAL_CAN_SHOW = min(LEVELS_BY_NAME.values())

SOURCE_MAIN = "main"
SOURCE_PORTRAIT = "portrait"
SOURCE_LANDSCAPE = "landscape"
SOURCE_DASH = "dash"
SOURCE_SYSTEM = "system"
HIS_WORD_FOR_THE_PORTRAIT_SATELLITE = "Sat"
SOURCE_LABELS: dict[str, str] = {
    "main": "Main",
    "portrait": HIS_WORD_FOR_THE_PORTRAIT_SATELLITE,
    "landscape": "Land",
    "dash": "Dash",
    "system": "Sys",
}

SOURCES: tuple[str, ...] = (
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_LANDSCAPE,
    SOURCE_DASH,
    SOURCE_SYSTEM,
)

EVENT_LOG_FILENAME = "event_log.jsonl"


@dataclass(frozen=True)
class EventRecord:
    ts: float
    level: int
    source: str
    message: str
    flashes: bool = True
    spoken_at: float | None = None
    mark: str = ""


def event_log_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / EVENT_LOG_FILENAME


def start_event_log(state_dir: str | Path) -> Path:
    path = event_log_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


class EventLogHandler(logging.Handler):
    def __init__(self, path: str | Path, *,
                 after_an_announcement: Callable[[], object] = lambda: None) -> None:
        super().__init__()
        self.path = Path(path)
        self._after_an_announcement = after_an_announcement

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = EventRecord(
                ts=record.created,
                level=record.levelno,
                source=getattr(record, "source", SOURCE_SYSTEM),
                message=record.getMessage(),
                flashes=getattr(record, "flashes", True),
                spoken_at=getattr(record, "spoken_at", None),
                mark=mark_of_a_message_said_now(getattr(record, "mark", "")),
            )
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(event_line(event) + "\n")
            if is_announcement(event):
                self._after_an_announcement()
        except Exception:
            pass


class _TheMessageAlone(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


class QueuedEventLog(QueueHandler):
    def __init__(self, *, writer: logging.Handler) -> None:
        super().__init__(queue.Queue())
        self.setFormatter(_TheMessageAlone())
        self.writer = writer
        self._listener = QueueListener(self.queue, writer)
        self._listener.start()

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        prepared = super().prepare(record)
        prepared.mark = mark_of_a_message_said_now(getattr(record, "mark", ""))
        return prepared

    def flush(self) -> None:
        self.queue.join()

    def close(self) -> None:
        self._listener.stop()
        super().close()


def queued_event_log(path: str | Path, *,
                     after_an_announcement: Callable[[], object] = lambda: None) -> QueuedEventLog:
    return QueuedEventLog(writer=EventLogHandler(path, after_an_announcement=after_an_announcement))


def event_line(record: EventRecord) -> str:
    payload: dict[str, object] = {
        "ts": record.ts, "level": record.level, "source": record.source, "msg": record.message,
    }
    if not record.flashes:
        payload["flash"] = False
    if record.spoken_at is not None:
        payload["spoken_at"] = record.spoken_at
    if record.mark:
        payload["mark"] = record.mark
    return json.dumps(payload, ensure_ascii=False)


def parse_event_line(line: str | bytes) -> EventRecord | None:
    try:
        payload = json.loads(line)
        return EventRecord(
            ts=float(payload["ts"]),
            level=int(payload["level"]),
            source=str(payload["source"]),
            message=str(payload["msg"]),
            flashes=payload.get("flash", True) is not False,
            spoken_at=float(payload["spoken_at"]) if "spoken_at" in payload else None,
            mark=str(payload.get("mark", "")),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def flash_message(logger: logging.Logger, message: str, *, source: str, level: int = MESSAGE,
                  mark: str = "") -> None:
    logger.log(level, message, extra={"source": source, "mark": mark})


def list_message(logger: logging.Logger, message: str, *, source: str, level: int = MESSAGE,
                 spoken_at: float | None = None) -> None:
    logger.log(level, message, extra={"source": source, "flashes": False,
                                      "spoken_at": spoken_at})


def is_announcement(record: EventRecord) -> bool:
    return record.flashes and record.level >= MESSAGE


def read_events(path: str | Path, offset: int = 0) -> tuple[list[EventRecord], int]:
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    a_new_session_cut_the_file_shorter = size < offset
    if a_new_session_cut_the_file_shorter:
        offset = 0
    if size == offset:
        return [], offset

    with path.open("rb") as fh:
        fh.seek(offset)
        blob = fh.read()

    consumed = blob.rfind(b"\n") + 1
    parsed = map(parse_event_line, blob[:consumed].splitlines())
    return [record for record in parsed if record is not None], offset + consumed


LOGGERS_THE_LISTENER_KEEPS_UNDER_ITS_OWN_NAME = ("voice_core",)

_NON_PROPAGATING_LOGGERS = ("fun_time.orchestrator",)


def open_event_log(state_dir: Path, *,
                   after_an_announcement: Callable[[], object] = lambda: None) -> None:
    handler = queued_event_log(start_event_log(state_dir),
                               after_an_announcement=after_an_announcement)
    handler.setLevel(EVERYTHING_THE_DIAL_CAN_SHOW)
    replaced: set[logging.Handler] = set()
    for name in ("fun_time", *_NON_PROPAGATING_LOGGERS, *LOGGERS_THE_LISTENER_KEEPS_UNDER_ITS_OWN_NAME):
        target = logging.getLogger(name)
        for existing in [h for h in target.handlers
                         if isinstance(h, (EventLogHandler, QueuedEventLog))]:
            target.removeHandler(existing)
            replaced.add(existing)
        target.addHandler(handler)
    for old in replaced:
        old.close()
    for name in ("fun_time", *LOGGERS_THE_LISTENER_KEEPS_UNDER_ITS_OWN_NAME):
        logging.getLogger(name).setLevel(EVERYTHING_THE_DIAL_CAN_SHOW)
