# Several processes log here, and an open-append-close write holds no Windows lock against the
# others.
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

MESSAGE = 25
logging.addLevelName(MESSAGE, "NOTICE")

FAVORITE = 26
logging.addLevelName(FAVORITE, "FAVORITE")

LEVELS_BY_NAME: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "NOTICE": MESSAGE,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
LEVEL_NAMES: tuple[str, ...] = tuple(LEVELS_BY_NAME)

SOURCE_MAIN = "main"
SOURCE_PORTRAIT = "portrait"
SOURCE_LANDSCAPE = "landscape"
SOURCE_DASH = "dash"
SOURCE_SYSTEM = "system"
SOURCE_LABELS: dict[str, str] = {
    "main": "Main",
    "portrait": "Sat",
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


def event_log_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / EVENT_LOG_FILENAME


def start_event_log(state_dir: str | Path) -> Path:
    path = event_log_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


class EventLogHandler(logging.Handler):
    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = event_line(EventRecord(
                ts=record.created,
                level=record.levelno,
                source=getattr(record, "source", SOURCE_SYSTEM),
                message=record.getMessage(),
                flashes=getattr(record, "flashes", True),
                spoken_at=getattr(record, "spoken_at", None),
            ))
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass


def event_line(record: EventRecord) -> str:
    payload: dict[str, object] = {
        "ts": record.ts, "level": record.level, "source": record.source, "msg": record.message,
    }
    if not record.flashes:
        payload["flash"] = False
    if record.spoken_at is not None:
        payload["spoken_at"] = record.spoken_at
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
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def flash_message(logger: logging.Logger, message: str, *, source: str, level: int = MESSAGE) -> None:
    logger.log(level, message, extra={"source": source})


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
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset

    with path.open("rb") as fh:
        fh.seek(offset)
        blob = fh.read()

    consumed = blob.rfind(b"\n") + 1
    parsed = map(parse_event_line, blob[:consumed].splitlines())
    return [record for record in parsed if record is not None], offset + consumed


THE_LISTENERS_LOGGERS = ("voice_core",)

_NON_PROPAGATING_LOGGERS = ("fun_time.orchestrator",)


def open_event_log(state_dir: Path) -> None:
    handler = EventLogHandler(start_event_log(state_dir))
    handler.setLevel(logging.DEBUG)
    for name in ("fun_time", *_NON_PROPAGATING_LOGGERS, *THE_LISTENERS_LOGGERS):
        target = logging.getLogger(name)
        for existing in [h for h in target.handlers if isinstance(h, EventLogHandler)]:
            target.removeHandler(existing)
        target.addHandler(handler)
    for name in ("fun_time", *THE_LISTENERS_LOGGERS):
        logging.getLogger(name).setLevel(logging.DEBUG)
