"""The glanceable flash of a notice (:func:`fun_time.event_log.notice`), at the
top-center of the window it is about: a main-player one over the Nau/Genau
display, a portrait/landscape one over that satellite.

The pure model here (which rect a source maps to, where the overlay sits on it)
is separated from the Qt window so it tests without a QApplication.
"""
from __future__ import annotations

from dataclasses import dataclass

from fun_time.dashboard_layout import Rect, Size
from fun_time.event_log import is_announcement

# Re-exported: notice_feed and the tests read the rule from here.
__all__ = ["PlayerRects", "is_announcement"]


@dataclass(frozen=True)
class PlayerRects:
    """Where each notice-bearing window sits on screen, in real coordinates."""

    main: Rect
    portrait: Rect
    landscape: Rect
    dash: Rect


def notice_target_rect(source: str, rects: PlayerRects) -> Rect:
    """The window a *source*'s notice flashes over.

    ``system`` (and any unexpected source) has no player of its own, so it falls
    back to the main player — the one always on screen.
    """
    return {
        "main": rects.main,
        "portrait": rects.portrait,
        "landscape": rects.landscape,
        "dash": rects.dash,
    }.get(source, rects.main)


def top_center_position(target: Rect, size: Size, *, margin: int) -> tuple[int, int]:
    """Top-left corner that centers a *size* overlay across *target*'s top.

    Clamped to the target's left edge so an overlay wider than its window never
    starts off to the left of it.
    """
    x = target.x + max(0, (target.width - size.width) // 2)
    y = target.y + margin
    return x, y


# ---------------------------------------------------------------------------
# PyQt6 window
# ---------------------------------------------------------------------------
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel
from shared_ui.colors import BG_SECONDARY
from shared_ui.fonts import FONT_UI, SIZE_HEADING, make_font

from fun_time.log_panel import level_color

# How long a flashed notice lingers before fading out.
NOTICE_LINGER_MS = 2200
# Gap from the top edge of the target window to the overlay.
NOTICE_TOP_MARGIN = 28


class NoticeOverlay(QLabel):
    """A frameless, click-through banner that flashes a notice over a player.

    It never takes focus — this suite is acutely focus-sensitive — so it is shown
    with ``WA_ShowWithoutActivating`` and the ``Tool`` window type, which Qt maps
    to a non-activating top-level on Windows.  Input falls through it
    (``WA_TransparentForMouseEvents``) so it cannot intercept a click meant for
    the player beneath.
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
