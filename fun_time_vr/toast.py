"""The banner a notice flashes over the player it names, drawn for a video.

The desktop hangs a frameless window over that player's; a headset has none, so
the same banner is composited into the picture the way the scrubber is.  Shape
and colors are :class:`fun_time.notice_overlay.NoticeOverlay`'s.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shared_ui.palette import BG_SECONDARY

from .console_panel import level_color

_FONT_PX = 19  # SIZE_HEADING at the dpi hud_panel.px() converts by
_PAD_X = 16
_PAD_Y = 8
_RADIUS = 4

# As a fraction of the height: a fixed pixel margin sits differently on a 4K
# master and a 720p clip.
TOP_MARGIN_FRACTION = 0.05


def _font() -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("segoeuib.ttf", _FONT_PX)
    except OSError:
        return ImageFont.load_default(_FONT_PX)


def fit_toast(font, text: str, width: int) -> str:
    """*text*, or as much of its head as draws inside *width* with an ellipsis."""
    if font.getlength(text) <= width or not text:
        return text
    kept = text
    while kept and font.getlength(kept + "…") > width:
        kept = kept[:-1]
    return kept + "…"


def paint_toast(message: str, level: int, *, max_width: int) -> Image.Image:
    """The banner itself, sized to what it says."""
    font = _font()
    text = fit_toast(font, message, max(1, max_width - 2 * _PAD_X))
    ink = level_color(level)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), text, font=font)
    banner = Image.new(
        "RGBA",
        (x1 - x0 + 2 * _PAD_X, y1 - y0 + 2 * _PAD_Y),
        (*BG_SECONDARY, 235),
    )
    draw = ImageDraw.Draw(banner)
    draw.rounded_rectangle(
        (0, 0, banner.width - 1, banner.height - 1), radius=_RADIUS, outline=(*ink, 255),
    )
    draw.text((_PAD_X - x0, _PAD_Y - y0), text, font=font, fill=(*ink, 255))
    return banner


def toast_placement(banner: Image.Image, width: int, height: int) -> tuple[int, int]:
    """Its top-left on a *width* x *height* picture: centered across the top,
    never off the left edge of a narrow one."""
    return max(0, (width - banner.width) // 2), int(height * TOP_MARGIN_FRACTION)


def toast_bgra(message: str, level: int, *, width: int, height: int):
    """``(x, y, bgra)`` for one overlay call, or None when it would not fit."""
    if width < 2 or height < 2:
        return None
    banner = paint_toast(message, level, max_width=width)
    x, y = toast_placement(banner, width, height)
    rgba = np.asarray(banner)
    return x, y, np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])
