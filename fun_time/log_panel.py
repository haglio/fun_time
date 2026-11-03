from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QTransform
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
    BG_BUTTON,
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    GREEN,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WHITE,
    YELLOW,
    hovered,
)
from shared_ui.fonts import FONT_UI, SIZE_SMALL, SIZE_TINY, make_font
from shared_ui.icons import icon_pixmap
from shared_ui.spacing import (
    BUTTON_GAP,
    BUTTON_GROUP_GAP,
    BUTTON_PAD_H_TIGHT,
    BUTTON_RADIUS_HUD,
    BUTTON_SIZE_HUD,
    GAP_SMALL,
    MARGIN_STANDARD,
)

from fun_time.event_log import (
    FAVORITE,
    LEVEL_NAMES,
    LEVELS_BY_NAME,
    MESSAGE,
    SOURCE_LABELS,
    SOURCES,
    EventRecord,
    event_log_path,
    read_events,
)
from fun_time.log_panel_model import (
    MAX_RECORDS,
    LogFilter,
    LogPanelState,
    append_records,
    copy_button_position,
    format_record,
    load_ui_state,
    remember_what_was_heard,
    save_ui_state,
    ui_state_path,
    visible_records,
    with_what_was_heard,
)

_SOURCE_LABELS = SOURCE_LABELS

_LEVEL_COLORS: dict[int, QColor] = {
    logging.DEBUG: TEXT_MUTED,
    logging.INFO: TEXT_MUTED,
    MESSAGE: TEXT_PRIMARY,
    FAVORITE: GREEN,
    logging.WARNING: YELLOW,
    logging.ERROR: RED,
}


def level_color(level: int) -> QColor:
    for threshold in sorted(_LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return _LEVEL_COLORS[threshold]
    return TEXT_PRIMARY


_COPY_BUTTON_SIZE = 18
_COPY_BUTTON_MARGIN = GAP_SMALL
_COPY_ICON_SIZE = 12
_COPY_FLASH_MS = 900


def _copy_icon(size: int, color: QColor) -> QIcon:
    return QIcon(icon_pixmap("copy", size, color))


def _copied_icon(size: int, color: QColor) -> QIcon:
    return QIcon(icon_pixmap("check", size, color))


_DIAL_ARROW = 10


class _LevelDial(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._arrow = icon_pixmap("chevron_right", _DIAL_ARROW, TEXT_MUTED).transformed(
            QTransform().rotate(90))

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.drawPixmap(self.width() - _DIAL_ARROW - BUTTON_GAP,
                           (self.height() - _DIAL_ARROW) // 2, self._arrow)
        painter.end()


class LogPanelWidget(QWidget):
    POLL_MS = 300

    @classmethod
    def for_session(cls, state_dir: Path) -> LogPanelWidget:
        return cls(event_log_path(state_dir), ui_state_path(state_dir))

    def __init__(
        self,
        event_log: Path,
        ui_state_file: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._event_log = Path(event_log)
        self._ui_state_file = Path(ui_state_file)
        self._offset = 0
        self._records: list[EventRecord] = []
        self._heard: dict[float, str] = {}
        self._hover_pos: QPoint | None = None

        state = load_ui_state(self._ui_state_file)
        self._filter = LogFilter(verbosity=state.verbosity, sources=state.sources)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(self.POLL_MS)
        self._poll()


    def _build_ui(self) -> None:
        self.setStyleSheet(f"background-color: {BG_PRIMARY.name()};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD)
        outer.setSpacing(GAP_SMALL)

        controls = QHBoxLayout()
        controls.setSpacing(0)
        outer.addLayout(controls)
        self._verbosity = _LevelDial(self)
        self._verbosity.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))
        self._verbosity.setFixedHeight(BUTTON_SIZE_HUD)
        self._verbosity.setStyleSheet(
            "QComboBox {"
            f" color: {TEXT_PRIMARY.name()};"
            f" background: {BG_BUTTON.name()};"
            f" border: 1px solid {TEXT_MUTED.name()};"
            f" padding: 0px {BUTTON_PAD_H_TIGHT}px;"
            f" border-radius: {BUTTON_RADIUS_HUD}px; }}"
            f" QComboBox:hover {{ background: {hovered(BG_BUTTON).name()}; }}"
            " QComboBox::drop-down { border: none;"
            f" width: {_DIAL_ARROW + 2 * BUTTON_GAP}px; }}"
            " QComboBox::down-arrow { image: none; }"
        )
        for name in LEVEL_NAMES:
            self._verbosity.addItem(name, LEVELS_BY_NAME[name])
        # Qt's own hint, taken AFTER the items and the sheet are in: a width
        # guessed at from the text plus a constant elided "WARNING" to "WARN".
        self._verbosity.setMinimumWidth(self._verbosity.sizeHint().width())
        self._verbosity.setCurrentText(logging.getLevelName(self._filter.verbosity))
        self._verbosity.currentIndexChanged.connect(self._on_verbosity_changed)
        controls.addWidget(self._verbosity)
        controls.addSpacing(BUTTON_GROUP_GAP)

        self._source_buttons: dict[str, QToolButton] = {}
        for source in SOURCES:
            button = QToolButton(self)
            button.setText(_SOURCE_LABELS[source])
            button.setToolTip(source)
            button.setCheckable(True)
            button.setChecked(source in self._filter.sources)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))
            label = QFontMetrics(button.font()).horizontalAdvance(button.text())
            button.setFixedSize(label + 2 * BUTTON_PAD_H_TIGHT, BUTTON_SIZE_HUD)
            button.setStyleSheet(
                "QToolButton {"
                f" color: {TEXT_PRIMARY.name()};"
                f" background: {BG_BUTTON.name()};"
                f" border: 1px solid {TEXT_MUTED.name()}; padding: 0px;"
                f" border-radius: {BUTTON_RADIUS_HUD}px; }}"
                f" QToolButton:hover {{ background: {hovered(BG_BUTTON).name()}; }}"
                f" QToolButton:checked {{ color: {WHITE.name()};"
                f" background: {BLUE.name()}; border-color: {BLUE.name()}; }}"
                f" QToolButton:checked:hover {{ background: {hovered(BLUE).name()}; }}"
            )
            # Auto-raise is what a QToolBar does: a flat row, not framed ones.
            button.setAutoRaise(True)
            button.toggled.connect(self._on_sources_changed)
            if self._source_buttons:
                controls.addSpacing(BUTTON_GAP)
            controls.addWidget(button)
            self._source_buttons[source] = button
        controls.addStretch(1)

        self._list = QListWidget(self)
        self._list.setFont(make_font(FONT_UI, SIZE_SMALL))
        self._list.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()}; border: none;"
        )
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setWordWrap(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._list.setMinimumWidth(0)
        outer.addWidget(self._list, stretch=1)
        self._build_copy_button()

    def _build_copy_button(self) -> None:
        viewport = self._list.viewport()
        self._copy_icon = _copy_icon(_COPY_ICON_SIZE, TEXT_PRIMARY)
        self._copied_icon = _copied_icon(_COPY_ICON_SIZE, TEXT_PRIMARY)

        self._copy_button = QToolButton(viewport)
        self._copy_button.setIcon(self._copy_icon)
        self._copy_button.setIconSize(QSize(_COPY_ICON_SIZE, _COPY_ICON_SIZE))
        self._copy_button.setFixedSize(_COPY_BUTTON_SIZE, _COPY_BUTTON_SIZE)
        self._copy_button.setToolTip("Copy this line")
        self._copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._copy_button.setStyleSheet(
            f"QToolButton {{ border: none; border-radius: {BUTTON_RADIUS_HUD}px;"
            f" background: {BG_BUTTON.name()}; }}"
            f" QToolButton:hover {{ background: {BLUE.name()}; }}"
        )
        self._copy_button.clicked.connect(self._copy_hovered_row)
        self._copy_button.hide()

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._restore_copy_icon)

        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)
        self._list.installEventFilter(self)
        self._list.verticalScrollBar().valueChanged.connect(self._sync_copy_button)


    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._list.viewport() and event.type() == QEvent.Type.MouseMove:
            self._hover_pos = event.position().toPoint()
            self._sync_copy_button()
        elif obj is self._list and event.type() == QEvent.Type.Leave:
            self._hover_pos = None
            self._sync_copy_button()
        return super().eventFilter(obj, event)

    def _hovered_item(self) -> QListWidgetItem | None:
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


    def _on_verbosity_changed(self) -> None:
        self._filter = LogFilter(
            verbosity=self._verbosity.currentData(),
            sources=self._filter.sources,
        )
        self._save_ui_state()
        self._rebuild_list()

    def _on_sources_changed(self) -> None:
        self._filter = LogFilter(
            verbosity=self._filter.verbosity,
            sources=frozenset(s for s, button in self._source_buttons.items() if button.isChecked()),
        )
        self._save_ui_state()
        self._rebuild_list()

    def _save_ui_state(self) -> None:
        save_ui_state(
            self._ui_state_file,
            LogPanelState(verbosity=self._filter.verbosity, sources=self._filter.sources),
        )

    def _poll(self) -> None:
        new, self._offset = read_events(self._event_log, self._offset)
        if not new:
            return
        self._records = append_records(self._records, new)
        self._rebuild_list()

    def show_what_was_heard(self, records: list[EventRecord]) -> None:
        self._heard = remember_what_was_heard(self._heard, records, keep=MAX_RECORDS)
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        at_lower = (
            self._list.verticalScrollBar().value()
            >= self._list.verticalScrollBar().maximum() - 4
        )
        self._list.clear()
        for record in visible_records(with_what_was_heard(self._records, self._heard),
                                      self._filter):
            item = QListWidgetItem(format_record(record))
            item.setForeground(level_color(record.level))
            self._list.addItem(item)
        if at_lower:
            self._list.scrollToBottom()
        self._sync_copy_button()


    def shutdown(self) -> None:
        self._timer.stop()
        self._flash_timer.stop()
