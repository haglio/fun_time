from __future__ import annotations

from PySide6.QtCore import QRect, QTimer
from shared_ui.message_window import MessageWindow

from fun_time.dashboard_layout import Rect
from fun_time.event_log import EventRecord
from fun_time.shown_message import ShownMessage


class MessageOverlay(MessageWindow):
    def __init__(self, **stacking) -> None:
        super().__init__(**stacking)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def flash(self, record: EventRecord, target: Rect, *, window: int | None = None) -> None:
        shown = ShownMessage.of(record)
        self.stay_above(window)
        self.say_over(QRect(target.x, target.y, target.width, target.height), shown.text,
                      kind=shown.kind, mark=shown.mark)
        self._hide_timer.start(shown.time_to_read_ms)

    def shutdown(self) -> None:
        self._hide_timer.stop()
        super().shutdown()
