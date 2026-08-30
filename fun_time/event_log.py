"""The one stream every Fun Time component's log lines land in.

Each line is a JSON object carrying the level, the message, and the *source* —
which of the four managed windows the line is about.  The log panel tails the
stream, filters it by source and verbosity, and shows the result beside the
dashboard; an agent debugging a session reads the same file.

Records are appended one JSON line at a time, reopening the file per write.
Several processes log into one session (orchestrator, dispatch loop, dashboard),
and an open-append-close write is what lets them share the file without one
holding a Windows lock against the others — the same trick the AHK bridge log
has always used.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

# A message meant for the person watching, not for a post-mortem: "Clip saved",
# "No other seeds".  Louder than INFO's diagnostic chatter, quieter than a
# WARNING.  These used to flash as AHK tooltips under the mouse; now they are
# what the log panel shows at its default verbosity.
NOTICE = 25
logging.addLevelName(NOTICE, "NOTICE")

# A notice about the one family of things green is reserved for on every surface
# in this app: the favorites, the lock that puts a clip in them, F-mode's filter
# over them, and the funscripts.  A hair louder than a plain NOTICE so it picks a
# color of its own without changing what any verbosity stop lets through — every
# notice, either kind, shows from the NOTICE stop down.  Not a stop on the dial
# itself: it is a NOTICE that happens to be about favorites, not a volume.
FAVORITE = 26
logging.addLevelName(FAVORITE, "FAVORITE")

# The verbosity dial's stops, least to most severe.
LEVELS_BY_NAME: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "NOTICE": NOTICE,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
LEVEL_NAMES: tuple[str, ...] = tuple(LEVELS_BY_NAME)

# Which window a line is about — the axis the log panel filters on.  Lines about
# the session as a whole (startup, voice, the broker) are SYSTEM.
SOURCE_MAIN = "main"
SOURCE_PORTRAIT = "portrait"
SOURCE_LANDSCAPE = "landscape"
SOURCE_DASH = "dash"
SOURCE_SYSTEM = "system"
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


def event_log_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / EVENT_LOG_FILENAME


def start_event_log(state_dir: str | Path) -> Path:
    """Truncate the event log for a fresh session and return its path.

    The panel and any later reader want *this* session, not the last one, and
    truncating per session is what keeps the file from growing without bound.
    """
    path = event_log_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


class EventLogHandler(logging.Handler):
    """Append each record to the event log as one JSON line.

    Reads the source off the record (``extra={"source": ...}``), defaulting to
    SOURCE_SYSTEM for the many lines that are not about one window.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = json.dumps(
                {
                    "ts": record.created,
                    "level": record.levelno,
                    "source": getattr(record, "source", SOURCE_SYSTEM),
                    "msg": record.getMessage(),
                },
                ensure_ascii=False,
            )
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # logging must never take the app down
            pass


def notice(logger: logging.Logger, message: str, *, source: str, level: int = NOTICE) -> None:
    """Log a message meant for the person watching the screen.

    *level* defaults to NOTICE (a normal announcement — white in the panel and
    the on-player flash); pass FAVORITE for one about the favorites or a
    funscript, which reads green, or a louder level (WARNING/ERROR) for a command
    that failed or hit a dead end, which reads amber/red.
    """
    logger.log(level, message, extra={"source": source})


def read_events(path: str | Path, offset: int = 0) -> tuple[list[EventRecord], int]:
    """Read the events appended after *offset*, returning them and a new offset.

    A file *shorter* than *offset* was truncated by a new session, so it is
    re-read from the start.  A truncation that happens to refill past *offset*
    between two calls would slip through, which no reader can see from an offset
    alone — and none does: :func:`start_event_log` truncates before the panel
    that tails it even exists, so a tail always starts at zero.

    A trailing fragment from a write still in flight is left unconsumed for the
    next call, and a line that does not parse is skipped — a tail that crashes on
    a torn write is worse than a tail that misses a line.
    """
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

    consumed = blob.rfind(b"\n") + 1  # 0 until the first line is complete
    records: list[EventRecord] = []
    for raw in blob[:consumed].splitlines():
        try:
            payload = json.loads(raw.decode("utf-8"))
            records.append(
                EventRecord(
                    ts=float(payload["ts"]),
                    level=int(payload["level"]),
                    source=str(payload["source"]),
                    message=str(payload["msg"]),
                )
            )
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            continue
    return records, offset + consumed
