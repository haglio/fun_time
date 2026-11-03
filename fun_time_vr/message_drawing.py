from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw
from shared_ui.lettering import fit_text, load_font, wrap_text
from shared_ui.message_shape import (
    MESSAGE_PAD_H,
    MESSAGE_PAD_V,
    MESSAGE_RADIUS,
    MESSAGE_TOP_MARGIN,
    MESSAGE_TYPE_SIZE,
)
from shared_ui.palette import BG_SECONDARY
from shared_ui.spacing import GAP_SMALL
from shared_ui.type_scale import in_pixels

from .main_hud import level_color
from .surfaces import red_and_blue_swapped

FONT_FRACTION = 0.030
MIN_FONT_PX = in_pixels(MESSAGE_TYPE_SIZE)
_PAD_X = MESSAGE_PAD_H / MIN_FONT_PX
_PAD_Y = MESSAGE_PAD_V / MIN_FONT_PX
_BORDER = 1 / MIN_FONT_PX
_RADIUS = MESSAGE_RADIUS / MIN_FONT_PX

_WINDOW_HEIGHT_PX = 1000
TOP_MARGIN_FRACTION = MESSAGE_TOP_MARGIN / _WINDOW_HEIGHT_PX

WIDTH_FRACTION = 0.9
MAX_LINES = 3
_LINE_GAP = GAP_SMALL / MIN_FONT_PX


def font_px(height: int) -> int:
    return max(MIN_FONT_PX, round(height * FONT_FRACTION))


def paint_message(message: str, level: int, *, max_width: int, size: int) -> Image.Image:
    font = load_font(size)
    pad_x, pad_y = round(size * _PAD_X), round(size * _PAD_Y)
    room = max(1, max_width - 2 * pad_x)
    text = "\n".join(fit_text(font, line, room)
                     for line in wrap_text(font, message, room, max_lines=MAX_LINES))
    spacing = round(size * _LINE_GAP)
    ink = level_color(level)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe.multiline_textbbox((0, 0), text, font=font, spacing=spacing,
                                              align="center")
    x0, y0, x1, y1 = math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)
    flashed = Image.new(
        "RGBA", (x1 - x0 + 2 * pad_x, y1 - y0 + 2 * pad_y), (*BG_SECONDARY, 235),
    )
    draw = ImageDraw.Draw(flashed)
    draw.rounded_rectangle(
        (0, 0, flashed.width - 1, flashed.height - 1),
        radius=max(2, round(size * _RADIUS)), outline=(*ink, 255),
        width=max(1, round(size * _BORDER)),
    )
    draw.multiline_text((pad_x - x0, pad_y - y0), text, font=font, fill=(*ink, 255),
                        spacing=spacing, align="center")
    return flashed


def message_placement_in_a_frame(flashed: Image.Image, width: int, height: int) -> tuple[int, int]:
    return max(0, (width - flashed.width) // 2), round(height * TOP_MARGIN_FRACTION)


def message_bgra(message: str, level: int, *, width: int, height: int):
    if width <= 1 or height <= 1:
        return None
    flashed = paint_message(
        message, level, max_width=round(width * WIDTH_FRACTION), size=font_px(height),
    )
    x, y = message_placement_in_a_frame(flashed, width, height)
    return x, y, red_and_blue_swapped(np.asarray(flashed))
