"""Which window a notice flashes over, and where on it -- read by the session's
own notice feed as well as by the overlay that draws one."""
from __future__ import annotations

from dataclasses import dataclass

from fun_time.dashboard_layout import Rect, Size


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
