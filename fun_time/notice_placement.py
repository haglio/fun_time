"""Which window a notice flashes over, where on it, and the shape it takes -- read
by the session's own notice feed, the overlay that draws one, and the headset's banner."""
from __future__ import annotations

from dataclasses import dataclass

from shared_ui.spacing import BUTTON_RADIUS, MARGIN_DIALOG, MARGIN_STANDARD
from shared_ui.type_scale import SIZE_HEADING

from fun_time.dashboard_layout import Rect, Size

NOTICE_TYPE_SIZE = SIZE_HEADING
NOTICE_PAD_V = MARGIN_STANDARD
NOTICE_PAD_H = MARGIN_DIALOG
NOTICE_RADIUS = BUTTON_RADIUS
# Gap from the top edge of the target window to the notice.
NOTICE_TOP_MARGIN = MARGIN_DIALOG


@dataclass(frozen=True)
class FunestraRects:
    """Where each notice-bearing window sits on screen, in real coordinates."""

    main: Rect
    portrait: Rect
    landscape: Rect
    dash: Rect


def notice_target_rect(source: str, rects: FunestraRects) -> Rect:
    """The window a *source*'s notice flashes over.

    ``system`` (and any unexpected source) has no Funestra of its own, so it falls
    back to the Main Funestra — the one always on screen.
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
