"""The banner a notice flashes over the Funestra it names, drawn for a video.

The desktop hangs a frameless window over that Funestra's; a headset has none, so
the same banner is composited into the picture the way the scrubber is.  Shape
and colors are :class:`fun_time.notice_overlay.NoticeOverlay`'s.

Every measurement scales with the picture, which is not the size of a window: a
satellite decodes to 2048px and the Main Funestra to 8192, so the desktop's own
19px type came out a tenth of the height it reads at there.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw
from shared_ui.lettering import fit_text, load_font, wrap_text
from shared_ui.notice_shape import (
    NOTICE_PAD_H,
    NOTICE_PAD_V,
    NOTICE_RADIUS,
    NOTICE_TOP_MARGIN,
    NOTICE_TYPE_SIZE,
)
from shared_ui.palette import BG_SECONDARY
from shared_ui.spacing import GAP_SMALL
from shared_ui.type_scale import in_pixels

from .console_panel import level_color
from .surfaces import red_and_blue_swapped

# The type as a fraction of the height, never under the desktop's own; the rest
# of the shape is the desktop's in multiples of it, so the proportions hold on any picture.
FONT_FRACTION = 0.030
MIN_FONT_PX = in_pixels(NOTICE_TYPE_SIZE)
_PAD_X = NOTICE_PAD_H / MIN_FONT_PX
_PAD_Y = NOTICE_PAD_V / MIN_FONT_PX
_BORDER = 1 / MIN_FONT_PX
_RADIUS = NOTICE_RADIUS / MIN_FONT_PX

# The desktop's own margin down a Funestra's window, as a fraction of one this tall.
_WINDOW_HEIGHT_PX = 1000
TOP_MARGIN_FRACTION = NOTICE_TOP_MARGIN / _WINDOW_HEIGHT_PX

WIDTH_FRACTION = 0.9  # most of the picture's width, never all of it
MAX_LINES = 3
_LINE_GAP = GAP_SMALL / MIN_FONT_PX


def font_px(height: int) -> int:  # the type size for a picture this tall
    return max(MIN_FONT_PX, round(height * FONT_FRACTION))


def paint_banner(message: str, level: int, *, max_width: int, size: int) -> Image.Image:
    """The banner, sized to what it says and to the picture it goes on."""
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
    banner = Image.new(
        "RGBA", (x1 - x0 + 2 * pad_x, y1 - y0 + 2 * pad_y), (*BG_SECONDARY, 235),
    )
    draw = ImageDraw.Draw(banner)
    draw.rounded_rectangle(
        (0, 0, banner.width - 1, banner.height - 1),
        radius=max(2, round(size * _RADIUS)), outline=(*ink, 255),
        width=max(1, round(size * _BORDER)),
    )
    draw.multiline_text((pad_x - x0, pad_y - y0), text, font=font, fill=(*ink, 255),
                        spacing=spacing, align="center")
    return banner


def banner_placement(banner: Image.Image, width: int, height: int) -> tuple[int, int]:
    """Centered across the top, never off a narrow picture's left edge."""
    return max(0, (width - banner.width) // 2), round(height * TOP_MARGIN_FRACTION)


def banner_bgra(message: str, level: int, *, width: int, height: int):
    """``(x, y, bgra)`` for one overlay call, or None when it would not fit."""
    if width < 2 or height < 2:
        return None
    banner = paint_banner(
        message, level, max_width=round(width * WIDTH_FRACTION), size=font_px(height),
    )
    x, y = banner_placement(banner, width, height)
    return x, y, red_and_blue_swapped(np.asarray(banner))
