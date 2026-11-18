from __future__ import annotations

from dataclasses import dataclass

from fun_time.dashboard_layout import Rect, Size


@dataclass(frozen=True)
class FunestraRects:
    main: Rect
    portrait: Rect
    landscape: Rect
    dash: Rect


def message_target_rect(source: str, rects: FunestraRects) -> Rect:
    return {
        "main": rects.main,
        "portrait": rects.portrait,
        "landscape": rects.landscape,
        "dash": rects.dash,
    }.get(source, rects.main)


def top_center_position(target: Rect, size: Size, *, margin: int) -> tuple[int, int]:
    x = target.x + max(0, (target.width - size.width) // 2)
    y = target.y + margin
    return x, y
