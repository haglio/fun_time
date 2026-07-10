"""The log panel — the strip beside the dashboard where the session narrates itself.

It tails :mod:`fun_time.event_log` and shows two things:

* a **banner** carrying the newest notice ("Clip saved", "No other seeds").  These
  used to flash as AHK tooltips under the mouse pointer, where they were easy to
  miss; here they sit in the top-left of the main display until the next one
  replaces them.
* the **stream** below it — every line the session logs, filtered by a verbosity
  dial and by which window the line is about.

The pure model (filter, buffer, formatting, prefs) sits above the Qt widgets so
it can be tested without a QApplication.
"""
from __future__ import annotations

import configparser
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from fun_time.event_log import (
    LEVEL_NAMES,
    LEVELS_BY_NAME,
    NOTICE,
    SOURCES,
    EventRecord,
    read_events,
)

# How many lines the panel keeps.  A long session logs more than anyone will
# scroll back through, and an unbounded list is an unbounded widget.
MAX_RECORDS = 2000

PREFS_FILENAME = "log_panel.ini"


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


def latest_notice(records: list[EventRecord], log_filter: LogFilter) -> EventRecord | None:
    """The newest line loud enough to be an announcement, from a shown source.

    Deliberately ignores the verbosity dial: a notice is what the old cursor
    tooltip used to be, and turning the dial to ERROR must not swallow
    "Clip saved".  Warnings and errors qualify too — they are louder still.
    """
    for record in reversed(records):
        if record.level >= NOTICE and record.source in log_filter.sources:
            return record
    return None


def format_record(record: EventRecord) -> str:
    clock = time.strftime("%H:%M:%S", time.localtime(record.ts))
    return f"{clock}  {record.source:<9}  {record.message}"


@dataclass(frozen=True)
class LogPanelPrefs:
    verbosity: int
    sources: frozenset[str]


DEFAULT_PREFS = LogPanelPrefs(verbosity=NOTICE, sources=frozenset(SOURCES))


def prefs_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / PREFS_FILENAME


def load_prefs(path: str | Path) -> LogPanelPrefs:
    """Read the panel's saved verbosity and source set, defaulting on any fault.

    A malformed prefs file must not stop the session's logs from being visible.
    """
    parser = configparser.ConfigParser()
    try:
        if not parser.read(str(path), encoding="utf-8"):
            return DEFAULT_PREFS
        section = parser["panel"]
        verbosity = int(section["verbosity"])
        sources = frozenset(s for s in section["sources"].split(",") if s in SOURCES)
    except (configparser.Error, KeyError, ValueError, OSError):
        return DEFAULT_PREFS
    return LogPanelPrefs(verbosity=verbosity, sources=sources)


def save_prefs(path: str | Path, prefs: LogPanelPrefs) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["panel"] = {
        "verbosity": str(prefs.verbosity),
        "sources": ",".join(sorted(prefs.sources)),
    }
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


# ---------------------------------------------------------------------------
# PyQt6 window
# ---------------------------------------------------------------------------
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from shared_ui.colors import (
    AMBER,
    BG_PRIMARY,
    BG_SECONDARY,
    GREEN,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_SMALL, make_font

from fun_time.win32 import is_window_topmost, set_always_on_top
from fun_time.window_roles import LOG_PANEL_WINDOW_TITLE

_LEVEL_COLORS: dict[int, QColor] = {
    logging.DEBUG: TEXT_MUTED,
    logging.INFO: TEXT_MUTED,
    NOTICE: GREEN,
    logging.WARNING: AMBER,
    logging.ERROR: RED,
}


def level_color(level: int) -> QColor:
    """The colour for *level*, rounding down to the loudest level it reaches."""
    for threshold in sorted(_LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return _LEVEL_COLORS[threshold]
    return TEXT_PRIMARY


class LogPanelWindow(QMainWindow):
    """Tails the event log beside the dashboard.

    Polls rather than watches: the writers are other processes appending to a
    shared file, and a 300ms poll of the bytes past our offset costs nothing next
    to the dashboard's own 500ms refresh.
    """

    POLL_MS = 300

    def __init__(
        self,
        event_log: Path,
        prefs_file: Path,
        *,
        geometry: tuple[int, int, int, int] | None = None,
    ) -> None:
        super().__init__()
        self._event_log = Path(event_log)
        self._prefs_file = Path(prefs_file)
        self._offset = 0
        self._records: list[EventRecord] = []

        prefs = load_prefs(self._prefs_file)
        self._filter = LogFilter(verbosity=prefs.verbosity, sources=prefs.sources)

        self.setWindowTitle(LOG_PANEL_WINDOW_TITLE)
        icon_path = Path(__file__).resolve().parent.parent / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self._build_ui()
        if geometry is not None:
            self.setGeometry(*geometry)

        self._hwnd = int(self.winId())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.POLL_MS)
        self._poll()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setStyleSheet(f"background-color: {BG_PRIMARY.name()};")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        self._banner = QLabel("", central)
        self._banner.setWordWrap(True)
        self._banner.setFont(make_font(FONT_UI, SIZE_BODY, bold=True))
        self._banner.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()}; color: {TEXT_PRIMARY.name()};"
            " padding: 6px; border-radius: 3px;"
        )
        outer.addWidget(self._banner)

        controls = QHBoxLayout()
        controls.setSpacing(4)
        self._verbosity = QComboBox(central)
        for name in LEVEL_NAMES:
            self._verbosity.addItem(name, LEVELS_BY_NAME[name])
        self._verbosity.setCurrentText(logging.getLevelName(self._filter.verbosity))
        self._verbosity.currentIndexChanged.connect(self._on_verbosity_changed)
        controls.addWidget(self._verbosity)

        self._source_boxes: dict[str, QCheckBox] = {}
        for source in SOURCES:
            box = QCheckBox(source, central)
            box.setChecked(source in self._filter.sources)
            box.setFont(make_font(FONT_UI, SIZE_SMALL))
            box.setStyleSheet(f"color: {TEXT_PRIMARY.name()};")
            box.stateChanged.connect(self._on_sources_changed)
            controls.addWidget(box)
            self._source_boxes[source] = box
        controls.addStretch(1)
        outer.addLayout(controls)

        self._list = QListWidget(central)
        self._list.setFont(make_font(FONT_UI, SIZE_SMALL))
        self._list.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()}; border: none;"
        )
        outer.addWidget(self._list, stretch=1)

        self.setCentralWidget(central)

    # -- live state --------------------------------------------------------

    def _on_verbosity_changed(self) -> None:
        self._filter = LogFilter(
            verbosity=self._verbosity.currentData(),
            sources=self._filter.sources,
        )
        self._save_prefs()
        self._rebuild_list()

    def _on_sources_changed(self) -> None:
        self._filter = LogFilter(
            verbosity=self._filter.verbosity,
            sources=frozenset(s for s, box in self._source_boxes.items() if box.isChecked()),
        )
        self._save_prefs()
        self._rebuild_list()

    def _save_prefs(self) -> None:
        save_prefs(
            self._prefs_file,
            LogPanelPrefs(verbosity=self._filter.verbosity, sources=self._filter.sources),
        )

    def _poll(self) -> None:
        new, self._offset = read_events(self._event_log, self._offset)
        if not new:
            return
        self._records = append_records(self._records, new)
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        at_bottom = (
            self._list.verticalScrollBar().value()
            >= self._list.verticalScrollBar().maximum() - 4
        )
        self._list.clear()
        for record in visible_records(self._records, self._filter):
            item = QListWidgetItem(format_record(record))
            item.setForeground(level_color(record.level))
            self._list.addItem(item)
        if at_bottom:
            self._list.scrollToBottom()

        announced = latest_notice(self._records, self._filter)
        self._banner.setText(announced.message if announced else "")
        self._banner.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()};"
            f" color: {(level_color(announced.level) if announced else TEXT_MUTED).name()};"
            " padding: 6px; border-radius: 3px;"
        )

    # -- window management -------------------------------------------------

    def sync_topmost(self, omni_paused: bool) -> None:
        """Track the dashboard's topmost band; OmniPause must free the desktop."""
        desired = not omni_paused
        if is_window_topmost(self._hwnd) != desired:
            set_always_on_top(self._hwnd, desired)

    def closeEvent(self, event: object) -> None:  # noqa: N802
        """The panel is part of the dashboard's furniture — it does not close."""
        event.ignore()
