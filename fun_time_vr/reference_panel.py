"""The hotkeys and voice reference, hanging in the headset: the desktop's is an
HTML popup and a Funestra process has no browser, so the same sections
(:func:`fun_time.command_reference.build_reference_sections`) are painted one at
a time, with two controls that walk them."""
from __future__ import annotations

from dataclasses import dataclass, replace

from PIL import Image, ImageDraw
from shared_ui.icons_pil import glyph_image
from shared_ui.lettering import fit_text, load_font, wrap_text
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    MAGENTA,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.spacing import BUTTON_MARK, BUTTON_RADIUS, BUTTON_SIZE

from fun_time.command_reference import (
    HOTKEY_HEADER,
    VOICE_HEADER,
    ReferenceSection,
    build_reference_sections,
)
from fun_time.dashboard_layout import Rect

__all__ = [
    "NEXT_PAGE",
    "PREV_PAGE",
    "REFERENCE_WIDTH_DEG",
    "REFERENCE_WIDTH_PX",
    "ReferencePointer",
    "ReferenceState",
    "header_lines",
    "note_lines",
    "page_of",
    "paint_reference",
    "reference_actions",
    "reference_height",
    "sections",
]

REFERENCE_WIDTH_PX = 760
REFERENCE_WIDTH_DEG = 54.0

PREV_PAGE = "reference_prev"
NEXT_PAGE = "reference_next"

_PAD = 12
_TITLE_PX = 17
_ROW_PX = 12
_ROW_H = 17
_HEAD_H = BUTTON_SIZE + _PAD
_NOTE_LINES_AT_MOST = 3
# The desktop table's three columns: press, say, and what it does.
_KEY_X, _SAY_X, _WHAT_X = _PAD, 210, 400
_NO_KEY = "—"


def sections():
    return build_reference_sections()


def note_lines(section: ReferenceSection) -> list[str]:
    if not section.note:
        return []
    return wrap_text(load_font(_ROW_PX), section.note, REFERENCE_WIDTH_PX - 2 * _PAD,
                     max_lines=_NOTE_LINES_AT_MOST)


def _note_lines_every_page_keeps() -> int:
    return max((len(note_lines(section)) for section in sections()), default=0)


def header_lines(section: ReferenceSection) -> int:
    """"Hotkey" alone, or with the Funestras' names under it where the column
    forks into one per Funestra."""
    return 1 + (len(section.key_headers) > 1)


def _header_lines_every_page_keeps() -> int:
    return max((header_lines(section) for section in sections()), default=0)


def _keys_text(row, section: ReferenceSection) -> str:
    """A row's keys as one line: each Funestra's under its name, a dash where it has none."""
    if len(section.key_headers) == 1:
        return " ".join(row.key_columns[0])
    return " / ".join(" ".join(column) or _NO_KEY for column in row.key_columns)


@dataclass(frozen=True)
class ReferenceState:
    open: bool = False
    page: int = 0


def page_of(state: ReferenceState) -> int:  # held inside the list, however far
    total = len(sections())
    return state.page % total if total else 0


def _lines_of(section: ReferenceSection) -> int:  # a title line per group, then its rows
    return len(section.groups) + len(section.rows)


def reference_height() -> int:  # the longest section's, so walking never resizes
    longest = max((_lines_of(section) for section in sections()), default=0)
    return _HEAD_H + _ROW_H * (_header_lines_every_page_keeps() + _note_lines_every_page_keeps()
                               + longest) + _PAD


def reference_actions() -> dict[str, Rect]:  # the two, at the top right
    y = _PAD
    right = REFERENCE_WIDTH_PX - _PAD - BUTTON_SIZE
    return {
        NEXT_PAGE: Rect(right, y, BUTTON_SIZE, BUTTON_SIZE),
        PREV_PAGE: Rect(right - BUTTON_SIZE - 6, y, BUTTON_SIZE, BUTTON_SIZE),
    }


def paint_reference(state: ReferenceState) -> Image.Image:
    """One section: its title, the heads, a row per command."""
    height = reference_height()
    panel = Image.new("RGBA", (REFERENCE_WIDTH_PX, height), (*BG_PRIMARY, 240))
    draw = ImageDraw.Draw(panel)
    title_font, row_font = load_font(_TITLE_PX), load_font(_ROW_PX)
    section = sections()[page_of(state)]

    draw.text((_PAD, _PAD), section.title, font=title_font, fill=(*MAGENTA, 255))
    for action, rect in reference_actions().items():
        draw.rounded_rectangle(
            (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
            radius=BUTTON_RADIUS, fill=(*BG_BUTTON, 255),
        )
        mark = "chevron_right" if action == NEXT_PAGE else "chevron_left"
        panel.alpha_composite(
            glyph_image(mark, BUTTON_MARK, TEXT_PRIMARY),
            (rect.x + (rect.width - BUTTON_MARK) // 2,
             rect.y + (rect.height - BUTTON_MARK) // 2),
        )

    y = _HEAD_H
    heads = (HOTKEY_HEADER, VOICE_HEADER, "What it does")
    for x, head in zip((_KEY_X, _SAY_X, _WHAT_X), heads, strict=True):
        draw.text((x, y), head, font=row_font, fill=(*TEXT_MUTED, 255))
    y += _ROW_H
    if header_lines(section) > 1:
        draw.text((_KEY_X, y), " / ".join(section.key_headers), font=row_font,
                  fill=(*TEXT_MUTED, 255))
        y += _ROW_H
    y += _ROW_H * (_header_lines_every_page_keeps() - header_lines(section))
    for line in note_lines(section):
        draw.text((_PAD, y), line, font=row_font, fill=(*TEXT_MUTED, 255))
        y += _ROW_H
    y += _ROW_H * (_note_lines_every_page_keeps() - len(note_lines(section)))

    for group in section.groups:
        draw.text((_PAD, y), group.title, font=row_font, fill=(*MAGENTA, 255))
        y += _ROW_H
        for row in group.rows:
            cells = (
                (_KEY_X, _keys_text(row, section), _SAY_X - _KEY_X - 8),
                (_SAY_X, ", ".join(row.voice), _WHAT_X - _SAY_X - 8),
                (_WHAT_X, row.description, REFERENCE_WIDTH_PX - _WHAT_X - _PAD),
            )
            for x, text, width in cells:
                draw.text((x, y), fit_text(row_font, text, width), font=row_font,
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
