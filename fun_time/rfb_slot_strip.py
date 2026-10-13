"""The strip under the dashboard's log: the Random Favs Browser or Origenerator
in the browser's place, drawn by the painter a Satellite Funestra's HUD draws
its Kino / Origenerator pair with."""
from __future__ import annotations

from functools import cache

from funestra.hud_button import FIT_THE_WORD, Button
from funestra.hud_panel import SYMBOL_FONT, draw_button, load_font, text_width
from funestra.satellite_hud import MODE_LABEL_PAD, button_row_rects
from PIL import Image, ImageDraw
from shared_ui.fonts import SIZE_BODY, SIZE_TINY
from shared_ui.palette import BG_PRIMARY

from .dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from .origenerator_switches import RfbSlot
from .satellite_buttons import STILL_STARTING_TOOLTIP

Rect = tuple[int, int, int, int]

_BUTTONS = (
    (RfbSlot.RFB, RFB_ACTIVATE, "Random Favs Browser",
     "The Random Favs Browser in its place"),
    (RfbSlot.ORIGENERATOR, ORIGENERATOR_ACTIVATE, "Origenerator",
     "Origenerator in the Random Favs Browser's place"),
)


def strip_buttons(rfb_slot: RfbSlot, *, origenerator_ready: bool) -> tuple[Button, ...]:
    def button(holder: RfbSlot, command: str, label: str, tooltip: str) -> Button:
        dim = holder is RfbSlot.ORIGENERATOR and not origenerator_ready
        return Button(command, label, STILL_STARTING_TOOLTIP if dim else tooltip,
                      width=FIT_THE_WORD, lit=rfb_slot is holder, dim=dim)
    return tuple(button(*declared) for declared in _BUTTONS)


@cache
def _word_font():
    return load_font(SIZE_TINY)


@cache
def _glyph_font():
    return load_font(SIZE_BODY, SYMBOL_FONT)


def place_strip(buttons: tuple[Button, ...], *, x: int, y: int) -> list[tuple[Rect, Button]]:
    widths = [text_width(_word_font(), button.glyph) + 2 * MODE_LABEL_PAD
              for button in buttons]
    return button_row_rects(x, y, buttons, widths)


def paint_strip(size: tuple[int, int], placed: list[tuple[Rect, Button]], *,
                hovered: Rect | None = None) -> Image.Image:
    image = Image.new("RGBA", size, (*BG_PRIMARY, 255))
    draw = ImageDraw.Draw(image)
    for rect, button in placed:
        draw_button(image, draw, rect, button, hovered=rect == hovered,
                    glyph_font=_glyph_font(), word_font=_word_font())
    return image
