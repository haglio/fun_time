from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication

THE_FOUR_EVENTS_OF_A_MOUSES_DOUBLE_CLICK = (
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonRelease,
    QEvent.Type.MouseButtonDblClick,
    QEvent.Type.MouseButtonRelease,
)


def send_left_button(view: QAbstractItemView, row: int, kind: QEvent.Type) -> None:
    point = QPointF(view.visualItemRect(view.item(row)).center())
    QApplication.sendEvent(view.viewport(), QMouseEvent(
        kind, point, view.viewport().mapToGlobal(point), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    ))


def a_double_click_as_the_mouse_sends_it(view: QAbstractItemView, row: int) -> None:
    for kind in THE_FOUR_EVENTS_OF_A_MOUSES_DOUBLE_CLICK:
        send_left_button(view, row, kind)
