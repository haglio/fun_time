"""The hotkeys and voice reference, hanging in the headset.

The desktop's is an HTML popup; a player process has no browser, so the same
sections (:func:`fun_time.command_reference.build_reference_sections`) are
painted one at a time, with two controls that walk them.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from PIL import Image, ImageDraw, ImageFont
from shared_ui.icons_pil import glyph_image
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    MAGENTA,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.spacing import BUTTON_ICON, BUTTON_RADIUS, BUTTON_SIZE

from fun_time.command_reference import build_reference_sections
from fun_time.dashboard_layout import Rect

__all__ = [
    "NEXT_PAGE",
    "PREV_PAGE",
    "REFERENCE_WIDTH_PX",
    "ReferencePointer",
    "ReferenceState",
    "page_of",
    "paint_reference",
    "reference_actions",
    "reference_height",
    "sections",
]

REFERENCE_WIDTH_PX = 760

PREV_PAGE = "reference_prev"
NEXT_PAGE = "reference_next"

_PAD = 12
_TITLE_PX = 17
_ROW_PX = 12
_ROW_H = 17
_HEAD_H = BUTTON_SIZE + _PAD
# The desktop table's three columns: press, say, and what it does.
_KEY_X, _SAY_X, _WHAT_X = _PAD, 210, 400


def _font(px: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("segoeuib.ttf", px)
    except OSError:
        return ImageFont.load_default(px)


def sections():
    return build_reference_sections()


@dataclass(frozen=True)
class ReferenceState:
    open: bool = False
    page: int = 0


def page_of(state: ReferenceState) -> int:  # held inside the list, however far
    total = len(sections())
    return state.page % total if total else 0


def reference_height() -> int:  # the longest section's, so walking never resizes
    longest = max((len(section.rows) for section in sections()), default=0)
    return _HEAD_H + _ROW_H * (longest + 2) + _PAD


def reference_actions() -> dict[str, Rect]:  # the two, at the top right
    y = _PAD
    right = REFERENCE_WIDTH_PX - _PAD - BUTTON_SIZE
    return {
        NEXT_PAGE: Rect(right, y, BUTTON_SIZE, BUTTON_SIZE),
        PREV_PAGE: Rect(right - BUTTON_SIZE - 6, y, BUTTON_SIZE, BUTTON_SIZE),
    }


def _fit(font, text: str, width: int) -> str:
    if font.getlength(text) <= width or not text:
        return text
    kept = text
    while kept and font.getlength(kept + "…") > width:
        kept = kept[:-1]
    return kept + "…"


def paint_reference(state: ReferenceState) -> Image.Image:
    """One section: its title, the heads, and a row per command."""
    height = reference_height()
    panel = Image.new("RGBA", (REFERENCE_WIDTH_PX, height), (*BG_PRIMARY, 240))
    draw = ImageDraw.Draw(panel)
    title_font, row_font = _font(_TITLE_PX), _font(_ROW_PX)
    section = sections()[page_of(state)]

    draw.text((_PAD, _PAD), section.title, font=title_font, fill=(*MAGENTA, 255))
    for action, rect in reference_actions().items():
        draw.rounded_rectangle(
            (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
            radius=BUTTON_RADIUS, fill=(*BG_BUTTON, 255),
        )
        mark = "chevron_right" if action == NEXT_PAGE else "chevron_left"
        panel.alpha_composite(
            glyph_image(mark, BUTTON_ICON, TEXT_PRIMARY),
            (rect.x + (rect.width - BUTTON_ICON) // 2,
             rect.y + (rect.height - BUTTON_ICON) // 2),
        )

    y = _HEAD_H
    heads = (" / ".join(section.key_headers), "Say", "What it does")
    for x, head in zip((_KEY_X, _SAY_X, _WHAT_X), heads, strict=True):
        draw.text((x, y), head, font=row_font, fill=(*TEXT_MUTED, 255))
    y += _ROW_H
    if section.note:
        draw.text((_PAD, y), _fit(row_font, section.note, REFERENCE_WIDTH_PX - 2 * _PAD),
                  font=row_font, fill=(*TEXT_MUTED, 255))
    y += _ROW_H

    for row in section.rows:
        keys = "  ".join(" ".join(column) for column in row.key_columns)
        cells = (
            (_KEY_X, keys, _SAY_X - _KEY_X - 8),
            (_SAY_X, ", ".join(row.voice), _WHAT_X - _SAY_X - 8),
            (_WHAT_X, row.description, REFERENCE_WIDTH_PX - _WHAT_X - _PAD),
        )
        for x, text, width in cells:
            draw.text((x, y), _fit(row_font, text, width), font=row_font,
                      fill=(*TEXT_PRIMARY, 255))
        y += _ROW_H
    return panel


class ReferencePointer:  # the two controls walk it; the rest is a page

    def __init__(self, state: ReferenceState | None = None) -> None:
        self.state = state or ReferenceState()

    def press(self, px: int, py: int) -> str | None:
        for action, rect in reference_actions().items():
            if (rect.x <= px < rect.x + rect.width
                    and rect.y <= py < rect.y + rect.height):
                step = 1 if action == NEXT_PAGE else -1
                self.state = replace(self.state, page=self.state.page + step)
                return action
        return None

    def showing(self, open_: bool) -> None:  # a fresh open starts at the front
        if open_ and not self.state.open:
            self.state = ReferenceState(open=True, page=0)
        else:
            self.state = replace(self.state, open=open_)
