from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from shared_ui.message_label import MessageLabel
from shared_ui.message_shape import MESSAGE_TOP_MARGIN, time_to_read_ms

from fun_time.dashboard_layout import Rect, Size
from fun_time.event_log import EventRecord
from fun_time.level_colors import message_kind
from fun_time.message_placement import top_center_position


class MessageOverlay(MessageLabel):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self._leave_focus_and_clicks_to_the_funestra_beneath()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def _leave_focus_and_clicks_to_the_funestra_beneath(self) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def flash(self, record: EventRecord, target: Rect) -> None:
        self.say(record.message, kind=message_kind(record.level), mark=record.mark)
        self.fit_into(target.width - 2 * MESSAGE_TOP_MARGIN)
        x, y = top_center_position(
            target, Size(self.width(), self.height()), margin=MESSAGE_TOP_MARGIN,
        )
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide_timer.start(time_to_read_ms(record.message))

    def shutdown(self) -> None:
        self._hide_timer.stop()
        self.hide()
        self.deleteLater()
