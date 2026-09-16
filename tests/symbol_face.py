from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont
from player_core.hud_panel import SYMBOL_FONT, load_font

_NO_FONT_HAS = "\U0010ffff"


def typed_in_the_symbol_face(face: str) -> bool:
    font = load_font(11, SYMBOL_FONT)
    return _rendered(font, face) != _rendered(font, _NO_FONT_HAS)


def _rendered(font: ImageFont.FreeTypeFont, text: str) -> bytes:
    image = Image.new("L", (64, 64))
    ImageDraw.Draw(image).text((0, 0), text, font=font, fill=255)
    return image.tobytes()
