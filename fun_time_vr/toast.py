"""The banner a notice flashes over the player it names, drawn for a video.

The desktop hangs a frameless window over that player's; a headset has none, so
the same banner is composited into the picture the way the scrubber is.  Shape
and colors are :class:`fun_time.notice_overlay.NoticeOverlay`'s.

Every measurement scales with the picture, which is not the size of a window: a
satellite decodes to 2048px and the main player to 4096, so the desktop's own
19px type came out a tenth of the height it reads at there.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shared_ui.palette import BG_SECONDARY

from .console_panel import level_color

# The type as a fraction of the height, never under the desktop's own; the rest
# of the shape is in multiples of it (the desktop's 16/8 padding, 1px border and
# 4px radius against its 19px type), so the proportions hold on any picture.
FONT_FRACTION = 0.030
MIN_FONT_PX = 19
_PAD_X = 0.84
_PAD_Y = 0.42
_BORDER = 1 / 19
_RADIUS = 4 / 19

# The desktop's own 28px down a player's window, as a fraction.
TOP_MARGIN_FRACTION = 0.028

WIDTH_FRACTION = 0.9  # most of the picture's width, never all of it


def font_px(height: int) -> int:  # the type size for a picture this tall
    return max(MIN_FONT_PX, round(height * FONT_FRACTION))


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("segoeuib.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def fit_toast(font, text: str, width: int) -> str:
    """*text*, or as much of its head as draws inside *width* with an ellipsis."""
    if font.getlength(text) <= width or not text:
        return text
    kept = text
    while kept and font.getlength(kept + "…") > width:
        kept = kept[:-1]
    return kept + "…"


def paint_toast(message: str, level: int, *, max_width: int, size: int) -> Image.Image:
    """The banner, sized to what it says and to the picture it goes on."""
    font = _font(size)
    pad_x, pad_y = round(size * _PAD_X), round(size * _PAD_Y)
    text = fit_toast(font, message, max(1, max_width - 2 * pad_x))
    ink = level_color(level)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), text, font=font)
    banner = Image.new(
        "RGBA", (x1 - x0 + 2 * pad_x, y1 - y0 + 2 * pad_y), (*BG_SECONDARY, 235),
    )
    draw = ImageDraw.Draw(banner)
    draw.rounded_rectangle(
        (0, 0, banner.width - 1, banner.height - 1),
        radius=max(2, round(size * _RADIUS)), outline=(*ink, 255),
        width=max(1, round(size * _BORDER)),
    )
    draw.text((pad_x - x0, pad_y - y0), text, font=font, fill=(*ink, 255))
    return banner


def toast_placement(banner: Image.Image, width: int, height: int) -> tuple[int, int]:
    """Centered across the top, never off a narrow picture's left edge."""
    return max(0, (width - banner.width) // 2), round(height * TOP_MARGIN_FRACTION)


def toast_bgra(message: str, level: int, *, width: int, height: int):
    """``(x, y, bgra)`` for one overlay call, or None when it would not fit."""
    if width < 2 or height < 2:
        return None
    banner = paint_toast(
        message, level, max_width=round(width * WIDTH_FRACTION), size=font_px(height),
    )
    x, y = toast_placement(banner, width, height)
    rgba = np.asarray(banner)
    return x, y, np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])
