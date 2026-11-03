from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel
from shared_ui.colors import BG_SECONDARY
from shared_ui.fonts import FONT_UI, make_font
from shared_ui.message_shape import (
    MESSAGE_PAD_H,
    MESSAGE_PAD_V,
    MESSAGE_RADIUS,
    MESSAGE_TOP_MARGIN,
    MESSAGE_TYPE_SIZE,
)

from fun_time.dashboard_layout import Rect, Size
from fun_time.event_log import EventRecord
from fun_time.log_panel import level_color
from fun_time.message_placement import top_center_position

MESSAGE_LINGER_MS = 2200


class MessageOverlay(QLabel):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFont(make_font(FONT_UI, MESSAGE_TYPE_SIZE, bold=True))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def flash(self, record: EventRecord, target: Rect) -> None:
        self.setText(record.message)
        self.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()};"
            f" color: {level_color(record.level).name()};"
            f" border: 1px solid {level_color(record.level).name()};"
            f" padding: {MESSAGE_PAD_V}px {MESSAGE_PAD_H}px; border-radius: {MESSAGE_RADIUS}px;"
        )
        self.adjustSize()
        x, y = top_center_position(
            target, Size(self.width(), self.height()), margin=MESSAGE_TOP_MARGIN,
        )
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide_timer.start(MESSAGE_LINGER_MS)

    def shutdown(self) -> None:
        self._hide_timer.stop()
        self.hide()
        self.deleteLater()
