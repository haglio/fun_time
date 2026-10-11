from __future__ import annotations

from collections.abc import Sequence

from PIL import Image, ImageDraw
from shared_ui.icons_pil import glyph_image
from shared_ui.palette import (
    BG_BUTTON,
    BG_TERTIARY,
    BLUE,
    BORDER_SUBTLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.spacing import BUTTON_GAP, BUTTON_PAD_H_TIGHT

from fun_time.dashboard_layout import Rect

from .slab import button, slab

ARROW_PX = 10


def arrow_down(size: int) -> Image.Image:
    return glyph_image("chevron_right", size, TEXT_MUTED).rotate(-90, expand=False)


def _written(pen: ImageDraw.ImageDraw, rect: Rect, text: str, font) -> None:
    pen.text((rect.x + BUTTON_PAD_H_TIGHT, rect.y + rect.height / 2), text, font=font,
             anchor="lm", fill=(*TEXT_PRIMARY, 255))


def paint_field(image: Image.Image, rect: Rect, text: str, font, *, lit: bool = False) -> None:
    pen = ImageDraw.Draw(image)
    button(pen, rect, BG_BUTTON, lit=lit)
    _written(pen, rect, text, font)
    image.alpha_composite(arrow_down(ARROW_PX), (rect.x + rect.width - ARROW_PX - BUTTON_GAP,
                                                 rect.y + (rect.height - ARROW_PX) // 2))


def paint_list(image: Image.Image, rows: Sequence[tuple[Rect, str, bool]], font) -> None:
    pen = ImageDraw.Draw(image)
    first, last = rows[0][0], rows[-1][0]
    frame = Rect(first.x, first.y, first.width, last.y + last.height - first.y)
    slab(pen, frame, BG_TERTIARY, BORDER_SUBTLE)
    for rect, text, chosen in rows:
        if chosen:
            pen.rectangle((rect.x + 1, rect.y, rect.x + rect.width - 2, rect.y + rect.height - 1),
                          fill=(*BLUE, 255))
        _written(pen, rect, text, font)
