"""The log panel — the strip beside the dashboard where the session narrates itself.

It tails :mod:`fun_time.event_log` and shows the session's log stream, filtered by
a verbosity dial and by which window each line is about.  The brief notices
("Clip saved", "No other seeds") flash over the player they concern — see
:mod:`fun_time.notice_overlay` — and also land here in the stream, coloured by
level, so the panel is the place to scroll back through everything that happened
— and, via the button that follows the cursor down the rows, to lift a line out
of.

The pure model (filter, buffer, formatting, prefs, button placement) sits above
the Qt widgets so it can be tested without a QApplication.
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
# PyQt6 widget
# ---------------------------------------------------------------------------
from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shared_ui.colors import (
    AMBER,
    BG_BUTTON,
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    GREEN,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.fonts import FONT_UI, SIZE_SMALL, make_font

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


# The hover copy button.  Small enough to sit within a single log row without
# swallowing it, and inset from the row's top-right corner.
_COPY_BUTTON_SIZE = 18
_COPY_BUTTON_MARGIN = 2
_COPY_ICON_SIZE = 12
# How long the button shows a tick instead of the sheets after a copy.  Without
# it a click produces no visible result at all and reads as having missed.
_COPY_FLASH_MS = 900


def _drawn_icon(size: int, color: QColor, draw) -> QIcon:
    """Run *draw* over a transparent square and hand back the result as an icon.

    The button's two glyphs are drawn rather than shipped as files: at 12px they
    are a handful of strokes, and a drawn one takes its colour from the palette
    instead of baking one into an asset.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    pen = QPen(color)
    pen.setWidthF(1.3)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(pen)
    try:
        draw(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def _copy_icon(size: int, color: QColor, background: QColor) -> QIcon:
    """The two-overlapping-sheets copy glyph.

    The front sheet is filled with *background* before it is stroked, so it
    occludes the back sheet's edges — outlines alone read as a lattice at this size.
    """
    def draw(painter: QPainter) -> None:
        inset = 1.0
        span = size - 2 * inset
        sheet = span * 0.72
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(inset + span - sheet, inset, sheet, sheet), 1.5, 1.5)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(inset, inset + span - sheet, sheet, sheet), 1.5, 1.5)

    return _drawn_icon(size, color, draw)


def _copied_icon(size: int, color: QColor) -> QIcon:
    """A tick — what the copy button shows for a moment after a successful copy."""
    def draw(painter: QPainter) -> None:
        painter.drawPolyline(
            QPointF(size * 0.20, size * 0.52),
            QPointF(size * 0.42, size * 0.74),
            QPointF(size * 0.80, size * 0.26),
        )

    return _drawn_icon(size, color, draw)


class LogPanelWidget(QWidget):
    """Tails the event log in the strip beside the dashboard schematic.

    A child of the dashboard window rather than a window of its own, so it rides
    the dashboard's topmost band, minimize/restore and close for free instead of
    being a second managed window the bridge has to track by title.

    Polls rather than watches: the writers are other processes appending to a
    shared file, and a 300ms poll of the bytes past our offset costs nothing next
    to the dashboard's own 500ms refresh.
    """

    POLL_MS = 300

    def __init__(
        self,
        event_log: Path,
        prefs_file: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._event_log = Path(event_log)
        self._prefs_file = Path(prefs_file)
        self._offset = 0
        self._records: list[EventRecord] = []
        # Where the cursor last was over the list, in viewport coordinates; None
        # once it has left.  The copy button's row is resolved from this.
        self._hover_pos: QPoint | None = None

        prefs = load_prefs(self._prefs_file)
        self._filter = LogFilter(verbosity=prefs.verbosity, sources=prefs.sources)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.POLL_MS)
        self._poll()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background-color: {BG_PRIMARY.name()};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # The verbosity dial and the five source toggles share one row.  The
        # toggles are compact checkable buttons with short labels (the full name
        # is the tooltip) rather than word-labelled checkboxes, so the whole row's
        # minimum width fits the strip instead of forcing the window wider.
        controls = QHBoxLayout()
        controls.setSpacing(2)
        self._verbosity = QComboBox(self)
        self._verbosity.setFont(make_font(FONT_UI, SIZE_SMALL))
        self._verbosity.setFixedWidth(80)
        for name in LEVEL_NAMES:
            self._verbosity.addItem(name, LEVELS_BY_NAME[name])
        self._verbosity.setCurrentText(logging.getLevelName(self._filter.verbosity))
        self._verbosity.currentIndexChanged.connect(self._on_verbosity_changed)
        controls.addWidget(self._verbosity)

        self._source_boxes: dict[str, QToolButton] = {}
        for source in SOURCES:
            button = QToolButton(self)
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

        self._list = QListWidget(self)
        self._list.setFont(make_font(FONT_UI, SIZE_SMALL))
        self._list.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()}; border: none;"
        )
        # The strip is narrow, so a long line wraps to as many rows as it needs
        # rather than being cut off with an ellipsis — the tail of a message
        # (the video name, the phrase heard) is exactly what the reader is after.
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setWordWrap(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._list.setMinimumWidth(0)
        outer.addWidget(self._list, stretch=1)
        self._build_copy_button()

    def _build_copy_button(self) -> None:
        """Add the one copy button that follows the cursor down the rows.

        A list item's text cannot be selected with the mouse, so getting a line
        out of the panel meant retyping it.  One floating button that moves to
        whichever row is hovered — rather than a button per row — is what keeps
        that affordance affordable: the buffer holds up to MAX_RECORDS lines and
        rebuilds whenever the tail advances, so per-row widgets would be rebuilt
        two thousand at a time, several times a minute.
        """
        viewport = self._list.viewport()
        self._copy_icon = _copy_icon(_COPY_ICON_SIZE, TEXT_PRIMARY, BG_BUTTON)
        self._copied_icon = _copied_icon(_COPY_ICON_SIZE, GREEN)

        self._copy_button = QToolButton(viewport)
        self._copy_button.setIcon(self._copy_icon)
        self._copy_button.setIconSize(QSize(_COPY_ICON_SIZE, _COPY_ICON_SIZE))
        self._copy_button.setFixedSize(_COPY_BUTTON_SIZE, _COPY_BUTTON_SIZE)
        self._copy_button.setToolTip("Copy this line")
        self._copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Takes no keyboard focus: it is a passing affordance over the text, and
        # this suite has paid enough for widgets that grab focus on a click.
        self._copy_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._copy_button.setStyleSheet(
            "QToolButton { border: none; border-radius: 3px;"
            f" background: {BG_BUTTON.name()}; }}"
            f" QToolButton:hover {{ background: {BLUE.name()}; }}"
        )
        self._copy_button.clicked.connect(self._copy_hovered_row)
        self._copy_button.hide()

        # The tick is restored by a timer this widget owns, so it cannot outlive
        # the button and fire against a deleted one.
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._restore_copy_icon)

        # MouseMove comes from the viewport; Leave has to come from the list
        # itself, because moving the cursor onto the button — a child of the
        # viewport — is already a Leave for the viewport, which would snatch the
        # button away the instant it was aimed at.
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)
        self._list.installEventFilter(self)
        self._list.verticalScrollBar().valueChanged.connect(self._sync_copy_button)

    # -- the hover copy button ---------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._list.viewport() and event.type() == QEvent.Type.MouseMove:
            self._hover_pos = event.position().toPoint()
            self._sync_copy_button()
        elif obj is self._list and event.type() == QEvent.Type.Leave:
            self._hover_pos = None
            self._sync_copy_button()
        return super().eventFilter(obj, event)

    def _hovered_item(self) -> QListWidgetItem | None:
        """The row under the remembered cursor position, resolved fresh each time.

        Never a stored item: the list is cleared and refilled on every tail
        advance, so a handle kept from the last hover would name a destroyed row.
        Resolving by position also means the button follows the rows as they
        scroll under a still cursor.
        """
        if self._hover_pos is None:
            return None
        return self._list.itemAt(self._hover_pos)

    def _sync_copy_button(self) -> None:
        item = self._hovered_item()
        if item is None:
            self._copy_button.hide()
            return
        viewport = self._list.viewport()
        x, y = copy_button_position(
            self._list.visualItemRect(item).top(),
            viewport.width(),
            viewport.height(),
            _COPY_BUTTON_SIZE,
            _COPY_BUTTON_MARGIN,
        )
        self._copy_button.move(x, y)
        self._copy_button.show()

    def _copy_hovered_row(self) -> None:
        item = self._hovered_item()
        if item is None:
            return
        QApplication.clipboard().setText(item.text())
        self._copy_button.setIcon(self._copied_icon)
        self._flash_timer.start(_COPY_FLASH_MS)

    def _restore_copy_icon(self) -> None:
        self._copy_button.setIcon(self._copy_icon)

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
        self._sync_copy_button()

    # -- lifecycle ---------------------------------------------------------

    def shutdown(self) -> None:
        """Stop tailing; the dashboard that owns this widget is going away.

        Only the timers need stopping — the widget itself is destroyed with its
        parent window.  It matters under test, where several dashboards are built
        and torn down in one process and a live poll would keep reading the file.
        """
        self._timer.stop()
        self._flash_timer.stop()
