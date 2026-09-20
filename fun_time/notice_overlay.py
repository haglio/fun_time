"""The glanceable flash of a notice (:func:`fun_time.event_log.notice`), at the
top-center of the window it is about: a main-slot one over the main player/Genau
display, a portrait/landscape one over that satellite.

Which window and where on it are :mod:`fun_time.notice_placement`'s.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# PyQt6 window
# ---------------------------------------------------------------------------
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel
from shared_ui.colors import BG_SECONDARY
from shared_ui.fonts import FONT_UI, SIZE_HEADING, make_font

from fun_time.dashboard_layout import Rect, Size
from fun_time.event_log import EventRecord
from fun_time.log_panel import level_color
from fun_time.notice_placement import top_center_position

# How long a flashed notice lingers before fading out.
NOTICE_LINGER_MS = 2200
# Gap from the top edge of the target window to the overlay.
NOTICE_TOP_MARGIN = 28


class NoticeOverlay(QLabel):
    """A frameless, click-through banner that flashes a notice over a player.

    It never takes focus -- this suite is acutely focus-sensitive -- so it is
    shown with ``WA_ShowWithoutActivating`` and the ``Tool`` window type, and
    input falls through it (``WA_TransparentForMouseEvents``) so it cannot
    intercept a click meant for the player under it.
    """

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
        self.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def flash(self, record: EventRecord, target: Rect) -> None:
        """Show *record*'s message over *target* for a moment, then hide."""
        self.setText(record.message)
        self.setStyleSheet(
            f"background-color: {BG_SECONDARY.name()};"
            f" color: {level_color(record.level).name()};"
            f" border: 1px solid {level_color(record.level).name()};"
            " padding: 8px 16px; border-radius: 4px;"
        )
        self.adjustSize()
        x, y = top_center_position(
            target, Size(self.width(), self.height()), margin=NOTICE_TOP_MARGIN,
        )
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide_timer.start(NOTICE_LINGER_MS)

    def shutdown(self) -> None:
        self._hide_timer.stop()
        self.hide()
        self.deleteLater()
