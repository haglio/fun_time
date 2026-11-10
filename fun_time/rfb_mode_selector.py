from __future__ import annotations

from collections.abc import Callable
from functools import cache

from funestra.hud import SYMBOL_FONT, ButtonLook, draw_button, load_font, text_width
from funestra.hud_button import FIT_THE_WORD, Button
from funestra.satellite_hud import MODE_LABEL_PAD, button_row_rects
from PIL import Image, ImageDraw
from shared_ui.fonts import SIZE_BODY, SIZE_TINY
from shared_ui.palette import BG_PRIMARY

from .dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from .rfb_modes import WHAT_THE_DIM_BUTTONS_SAY, RfbMode

Rect = tuple[int, int, int, int]

STILL_STARTING_TOOLTIP = f"{WHAT_THE_DIM_BUTTONS_SAY} — this button becomes enabled when it's ready"

_BUTTONS = (
    (RfbMode.RFB, RFB_ACTIVATE, "Random Faves Browser"),
    (RfbMode.ORIGENERATOR, ORIGENERATOR_ACTIVATE, "Origenerator"),
)


def selector_buttons(rfb_mode: RfbMode, *, origenerator_core_ready: bool) -> tuple[Button, ...]:
    def button(holder: RfbMode, command: str, label: str) -> Button:
        dim = holder is RfbMode.ORIGENERATOR and not origenerator_core_ready
        return Button(command, label, STILL_STARTING_TOOLTIP if dim else "",
                      width=FIT_THE_WORD, lit=rfb_mode is holder, dim=dim)
    return tuple(button(*declared) for declared in _BUTTONS)


@cache
def _word_font():
    return load_font(SIZE_TINY)


@cache
def _icon_font():
    return load_font(SIZE_BODY, SYMBOL_FONT)


def place_selector(buttons: tuple[Button, ...], *, x: int, y: int) -> list[tuple[Rect, Button]]:
    widths = [text_width(_word_font(), button.icon) + 2 * MODE_LABEL_PAD
              for button in buttons]
    return button_row_rects(x, y, buttons, widths)


def paint_selector(size: tuple[int, int], placed: list[tuple[Rect, Button]], *,
                hovered: Rect | None = None) -> Image.Image:
    image = Image.new("RGBA", size, (*BG_PRIMARY, 255))
    draw = ImageDraw.Draw(image)
    for rect, button in placed:
        draw_button(image, draw, rect, button, ButtonLook(
            hovered=rect == hovered, icon_font=_icon_font(), word_font=_word_font()))
    return image


class RfbModeSelector:
    def __init__(self, *, x: int, y: int, post: Callable[[str], None]) -> None:
        self._x, self._y = x, y
        self._post = post
        self.placed: list[tuple[Rect, Button]] = []
        self._hovered: tuple[Rect, Button] | None = None

    def show(self, rfb_mode: RfbMode, *, origenerator_core_ready: bool) -> bool:
        placed = place_selector(selector_buttons(rfb_mode, origenerator_core_ready=origenerator_core_ready),
                                x=self._x, y=self._y)
        changed = placed != self.placed
        self.placed = placed
        return changed

    @property
    def name_under_the_pointer(self) -> str | None:
        return None if self._hovered is None else self._hovered[1].tooltip_on_hover

    def rgba(self, size: tuple[int, int]) -> bytes:
        hovered = None if self._hovered is None else self._hovered[0]
        return paint_selector(size, self.placed, hovered=hovered).tobytes("raw", "RGBA")

    def press(self, x: int, y: int) -> None:
        under = self._under(x, y)
        if under is not None and not under[1].dim:
            self._post(under[1].command)

    def move(self, x: int, y: int) -> None:
        self._hovered = self._under(x, y)

    def release(self) -> None:
        pass

    def leave(self) -> None:
        self._hovered = None

    def _under(self, x: int, y: int) -> tuple[Rect, Button] | None:
        return next(((rect, button) for rect, button in self.placed
                     if rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]),
                    None)
