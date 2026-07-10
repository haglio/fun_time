"""The log panel — the strip beside the dashboard where the session narrates itself.

It tails :mod:`fun_time.event_log` and shows the session's log stream, filtered by
a verbosity dial and by which window each line is about.  The brief notices
("Clip saved", "No other seeds") flash over the player they concern — see
:mod:`fun_time.notice_overlay` — and also land here in the stream, coloured by
level, so the panel is the place to scroll back through everything that happened.

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
    QComboBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# Short labels for the source toggles so the whole control strip fits one row.
# The full source name is the tooltip.  "Sat" is the user's word for the portrait
# satellite; landscape is named in full because they treat it as its own thing.
_SOURCE_LABELS: dict[str, str] = {
    "primary": "Prm",
    "portrait": "Sat",
    "landscape": "Land",
    "dash": "Dash",
    "system": "Sys",
}

from shared_ui.colors import (
    AMBER,
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    GREEN,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.fonts import FONT_UI, SIZE_SMALL, make_font

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
        self._disposing = False

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
            x, y, width, height = geometry
            # Pin the panel to its strip.  Left to itself Qt would grow the window
            # to the controls' minimum width — on the real desktop that came out
            # 523px against a 312px strip — and the panel would sit over the
            # landscape player.  The strip is exactly as wide as the layout says,
            # so the window takes it and the controls fit themselves in.
            self.setFixedSize(width, height)
            self.setGeometry(x, y, width, height)

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

        # The verbosity dial and the five source toggles share one row.  The
        # toggles are compact checkable buttons with short labels (the full name
        # is the tooltip) rather than word-labelled checkboxes, so the whole row's
        # minimum width fits the strip instead of forcing the window wider.
        controls = QHBoxLayout()
        controls.setSpacing(2)
        self._verbosity = QComboBox(central)
        self._verbosity.setFont(make_font(FONT_UI, SIZE_SMALL))
        self._verbosity.setFixedWidth(80)
        for name in LEVEL_NAMES:
            self._verbosity.addItem(name, LEVELS_BY_NAME[name])
        self._verbosity.setCurrentText(logging.getLevelName(self._filter.verbosity))
        self._verbosity.currentIndexChanged.connect(self._on_verbosity_changed)
        controls.addWidget(self._verbosity)

        self._source_boxes: dict[str, QToolButton] = {}
        for source in SOURCES:
            button = QToolButton(central)
            button.setText(_SOURCE_LABELS[source])
            button.setToolTip(source)
            button.setCheckable(True)
            button.setChecked(source in self._filter.sources)
            button.setFont(make_font(FONT_UI, SIZE_SMALL))
            # Fixed narrow width: five toggles plus the dial share one row across a
            # ~300px strip, so each must give up the space QToolButton would
            # otherwise reserve.
            button.setFixedWidth(40)
            button.setStyleSheet(
                "QToolButton { padding: 2px 1px; border: none;"
                f" color: {TEXT_MUTED.name()}; background: {BG_SECONDARY.name()}; border-radius: 2px; }}"
                f" QToolButton:checked {{ color: {TEXT_PRIMARY.name()}; background: {BLUE.name()}; }}"
            )
            button.toggled.connect(self._on_sources_changed)
            controls.addWidget(button)
            self._source_boxes[source] = button
        controls.addStretch(1)
        outer.addLayout(controls)

        self._list = QListWidget(central)
        self._list.setFont(make_font(FONT_UI, SIZE_SMALL))
        self._list.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()}; border: none;"
        )
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setMinimumWidth(0)
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
            sources=frozenset(s for s, button in self._source_boxes.items() if button.isChecked()),
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

    # -- window management -------------------------------------------------

    def sync_topmost(self, omni_paused: bool) -> None:
        """Track the dashboard's topmost band; OmniPause must free the desktop."""
        desired = not omni_paused
        if is_window_topmost(self._hwnd) != desired:
            set_always_on_top(self._hwnd, desired)

    def shutdown(self) -> None:
        """Stop tailing and dispose of the window; the dashboard is going away."""
        self._timer.stop()
        self._disposing = True
        self.close()
        self.deleteLater()

    def closeEvent(self, event: object) -> None:  # noqa: N802
        """The panel is furniture: only the dashboard's shutdown closes it.

        Ignoring the close means Alt+F4 on the panel cannot leave the session
        without the one surface that says what it is doing.
        """
        if self._disposing:
            event.accept()
        else:
            event.ignore()
