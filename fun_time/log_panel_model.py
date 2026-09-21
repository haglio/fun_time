"""What the log panel shows, before anything draws it; the widget that draws it
is :mod:`fun_time.log_panel`."""
from __future__ import annotations

import configparser
import time
from dataclasses import dataclass
from pathlib import Path

from fun_time.event_log import (
    NOTICE,
    SOURCES,
    EventRecord,
)

# How many lines the panel keeps.  A long session logs more than anyone will
# scroll back through, and an unbounded list is an unbounded widget.
MAX_RECORDS = 2000

UI_STATE_FILENAME = "log_panel_state.ini"


@dataclass(frozen=True)
class LogFilter:
    """What the panel is currently showing: a verbosity floor and a source set."""

    verbosity: int
    sources: frozenset[str]

    def accepts(self, record: EventRecord) -> bool:
        return record.level >= self.verbosity and record.source in self.sources


def visible_records(records: list[EventRecord], log_filter: LogFilter) -> list[EventRecord]:
    return [r for r in records if log_filter.accepts(r)]


def append_records(buffer: list[EventRecord], new: list[EventRecord]) -> list[EventRecord]:
    """Append *new* to *buffer*, dropping the oldest lines past the cap."""
    combined = buffer + new
    return combined[-MAX_RECORDS:]


def format_record(record: EventRecord) -> str:
    clock = time.strftime("%H:%M:%S", time.localtime(record.ts))
    return f"{clock}  {record.source:<9}  {record.message}"


def copy_button_position(
    row_top: int,
    viewport_width: int,
    viewport_height: int,
    button_size: int,
    margin: int,
) -> tuple[int, int]:
    """Where the hover copy button sits for the row whose top is at *row_top*.

    Right-aligned in the viewport (which excludes the scrollbar) and pinned to the
    row's top rather than its middle, so a message long enough to wrap over three
    rows still puts the button where the line begins.  The row under the cursor is
    routinely half-scrolled past an edge, so the button is clamped to stay wholly
    inside the viewport instead of being drawn where it cannot be clicked.
    """
    last_y = viewport_height - button_size - margin
    return (
        viewport_width - button_size - margin,
        max(margin, min(row_top + margin, last_y)),
    )


@dataclass(frozen=True)
class LogPanelState:
    verbosity: int
    sources: frozenset[str]


DEFAULT_UI_STATE = LogPanelState(verbosity=NOTICE, sources=frozenset(SOURCES))


def ui_state_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / UI_STATE_FILENAME


def load_ui_state(path: str | Path) -> LogPanelState:
    """Read the panel's saved verbosity and source set, defaulting on any fault.

    A malformed state file must not stop the session's logs from being visible.
    """
    parser = configparser.ConfigParser()
    try:
        if not parser.read(str(path), encoding="utf-8"):
            return DEFAULT_UI_STATE
        section = parser["panel"]
        verbosity = int(section["verbosity"])
        sources = frozenset(s for s in section["sources"].split(",") if s in SOURCES)
    except (configparser.Error, KeyError, ValueError, OSError):
        return DEFAULT_UI_STATE
    return LogPanelState(verbosity=verbosity, sources=sources)


def save_ui_state(path: str | Path, state: LogPanelState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["panel"] = {
        "verbosity": str(state.verbosity),
        "sources": ",".join(sorted(state.sources)),
    }
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)
