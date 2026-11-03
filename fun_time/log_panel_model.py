from __future__ import annotations

import configparser
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from fun_time.event_log import (
    MESSAGE,
    SOURCES,
    EventRecord,
)

MAX_RECORDS = 2000

UI_STATE_FILENAME = "log_panel_state.ini"


@dataclass(frozen=True)
class LogFilter:
    verbosity: int
    sources: frozenset[str]

    def accepts(self, record: EventRecord) -> bool:
        return record.level >= self.verbosity and record.source in self.sources


def visible_records(records: list[EventRecord], log_filter: LogFilter) -> list[EventRecord]:
    return [r for r in records if log_filter.accepts(r)]


def append_records(buffer: list[EventRecord], new: list[EventRecord]) -> list[EventRecord]:
    combined = buffer + new
    return combined[-MAX_RECORDS:]


def remember_what_was_heard(heard: Mapping[float, str], records: Iterable[EventRecord], *,
                            keep: int) -> dict[float, str]:
    remembered = {**heard, **{record.spoken_at: record.message for record in records
                              if record.spoken_at is not None}}
    return dict(list(remembered.items())[-keep:])


def with_what_was_heard(records: list[EventRecord],
                        heard: Mapping[float, str]) -> list[EventRecord]:
    return [replace(record, message=heard[record.spoken_at])
            if record.spoken_at in heard else record for record in records]


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
    last_y = viewport_height - button_size - margin
    return (
        viewport_width - button_size - margin,
        max(margin, min(row_top + margin, last_y)),
    )


@dataclass(frozen=True)
class LogPanelState:
    verbosity: int
    sources: frozenset[str]


DEFAULT_UI_STATE = LogPanelState(verbosity=MESSAGE, sources=frozenset(SOURCES))


def ui_state_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / UI_STATE_FILENAME


def load_ui_state(path: str | Path) -> LogPanelState:
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
