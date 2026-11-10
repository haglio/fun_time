from __future__ import annotations

from dataclasses import dataclass, replace

from PIL import Image, ImageDraw
from shared_ui.icons_pil import pil_icon
from shared_ui.lettering import fit_text, load_font, wrap_text
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    MAGENTA,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.reference import CONTROLLER_HEADER, HOTKEY_HEADER, VOICE_HEADER, ReferenceSection
from shared_ui.spacing import BUTTON_FILLING_ICON, BUTTON_RADIUS, BUTTON_SIZE, MARGIN_DIALOG
from shared_ui.type_scale import SIZE_HEADING, SIZE_SMALL, in_pixels

from fun_time.command_reference import build_reference_sections
from fun_time.dashboard_layout import Rect

__all__ = [
    "NEXT_PAGE",
    "PREV_PAGE",
    "REFERENCE_WIDTH_DEG",
    "REFERENCE_WIDTH_PX",
    "ReferencePointer",
    "ReferenceState",
    "column_heads",
    "header_lines",
    "lines_of",
    "note_lines",
    "page_of",
    "paint_reference",
    "reference_actions",
    "reference_height",
]

REFERENCE_WIDTH_PX = 760
REFERENCE_WIDTH_DEG = 54.0

PREV_PAGE = "reference_prev"
NEXT_PAGE = "reference_next"

_PAD = MARGIN_DIALOG
_TITLE_PX = in_pixels(SIZE_HEADING)
_ROW_PX = in_pixels(SIZE_SMALL)
_ROW_H = 17
_HEAD_H = BUTTON_SIZE + _PAD
_NOTE_LINES_AT_MOST = 3
_KEY_X, _SAY_X, _WHAT_X = _PAD, 210, 400
_CONTROLLER_X, _SAY_AFTER_CONTROLLER_X, _WHAT_AFTER_BOTH_X = 170, 300, 470
_THE_FRONT_NOT_WHERE_READING_STOPPED = 0
_NO_KEY = "—"


Pages = tuple[ReferenceSection, ...]


def _the(pages: Pages | None) -> Pages:
    return build_reference_sections() if pages is None else pages


def note_lines(section: ReferenceSection) -> list[str]:
    if not section.note:
        return []
    return wrap_text(load_font(_ROW_PX), " ".join(section.note.split()),
                     REFERENCE_WIDTH_PX - 2 * _PAD, max_lines=_NOTE_LINES_AT_MOST)


def _note_lines_every_page_keeps(pages: Pages) -> int:
    return max((len(note_lines(section)) for section in pages), default=0)


def header_lines(section: ReferenceSection) -> int:
    return 1 + (len(section.key_headers) > 1 and section.has_keys)


def _header_lines_every_page_keeps(pages: Pages) -> int:
    return max((header_lines(section) for section in pages), default=0)


def column_heads(section: ReferenceSection) -> tuple[tuple[int, str], ...]:
    if section.has_keys and section.has_controller:
        return ((_KEY_X, HOTKEY_HEADER), (_CONTROLLER_X, CONTROLLER_HEADER),
                (_SAY_AFTER_CONTROLLER_X, VOICE_HEADER), (_WHAT_AFTER_BOTH_X, "What it does"))
    if section.has_keys:
        return ((_KEY_X, HOTKEY_HEADER), (_SAY_X, VOICE_HEADER), (_WHAT_X, "What it does"))
    if section.has_controller:
        return ((_KEY_X, CONTROLLER_HEADER), (_CONTROLLER_X, VOICE_HEADER), (_WHAT_X, "What it does"))
    return ((_KEY_X, VOICE_HEADER), (_WHAT_X, "What it does"))


def _keys_text(row, section: ReferenceSection) -> str:
    if len(section.key_headers) == 1:
        return " ".join(row.key_columns[0])
    return " / ".join(" ".join(column) or _NO_KEY for column in row.key_columns)


def _cells(row, section: ReferenceSection) -> tuple[tuple[int, str, int], ...]:
    texts = (", ".join(row.voice), row.description)
    if section.has_controller:
        texts = (", ".join(row.controller), *texts)
    if section.has_keys:
        texts = (_keys_text(row, section), *texts)
    xs = [x for x, _ in column_heads(section)]
    ends = [*xs[1:], REFERENCE_WIDTH_PX - _PAD + 8]
    return tuple((x, text, end - x - 8) for x, end, text in zip(xs, ends, texts, strict=True))


@dataclass(frozen=True)
class ReferenceState:
    open: bool = False
    page: int = 0


def page_of(state: ReferenceState, pages: Pages | None = None) -> int:
    total = len(_the(pages))
    return state.page % total if total else 0


def lines_of(section: ReferenceSection) -> int:
    return sum(1 for group in section.groups if group.title) + len(section.rows)


def reference_height(pages: Pages | None = None) -> int:
    pages = _the(pages)
    longest = max((lines_of(section) for section in pages), default=0)
    return _HEAD_H + _ROW_H * (_header_lines_every_page_keeps(pages)
                               + _note_lines_every_page_keeps(pages) + longest) + _PAD


def reference_actions() -> dict[str, Rect]:
    y = _PAD
    right = REFERENCE_WIDTH_PX - _PAD - BUTTON_SIZE
    return {
        NEXT_PAGE: Rect(right, y, BUTTON_SIZE, BUTTON_SIZE),
        PREV_PAGE: Rect(right - BUTTON_SIZE - 6, y, BUTTON_SIZE, BUTTON_SIZE),
    }


def paint_reference(state: ReferenceState, pages: Pages | None = None) -> Image.Image:
    pages = _the(pages)
    height = reference_height(pages)
    panel = Image.new("RGBA", (REFERENCE_WIDTH_PX, height), (*BG_PRIMARY, 240))
    draw = ImageDraw.Draw(panel)
    title_font, row_font = load_font(_TITLE_PX), load_font(_ROW_PX)
    section = pages[page_of(state, pages)]

    draw.text((_PAD, _PAD), section.title, font=title_font, fill=(*MAGENTA, 255))
    for action, rect in reference_actions().items():
        draw.rounded_rectangle(
            (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
            radius=BUTTON_RADIUS, fill=(*BG_BUTTON, 255),
        )
        mark = "chevron_right" if action == NEXT_PAGE else "chevron_left"
        panel.alpha_composite(
            pil_icon(mark, BUTTON_FILLING_ICON, TEXT_PRIMARY),
            (rect.x + (rect.width - BUTTON_FILLING_ICON) // 2,
             rect.y + (rect.height - BUTTON_FILLING_ICON) // 2),
        )

    y = _HEAD_H
    for x, head in column_heads(section):
        draw.text((x, y), head, font=row_font, fill=(*TEXT_MUTED, 255))
    y += _ROW_H
    if header_lines(section) > 1:
        draw.text((_KEY_X, y), " / ".join(section.key_headers), font=row_font,
                  fill=(*TEXT_MUTED, 255))
        y += _ROW_H
    y += _ROW_H * (_header_lines_every_page_keeps(pages) - header_lines(section))
    for line in note_lines(section):
        draw.text((_PAD, y), line, font=row_font, fill=(*TEXT_MUTED, 255))
        y += _ROW_H
    y += _ROW_H * (_note_lines_every_page_keeps(pages) - len(note_lines(section)))

    for group in section.groups:
        if group.title:
            draw.text((_PAD, y), group.title, font=row_font, fill=(*MAGENTA, 255))
            y += _ROW_H
        for row in group.rows:
            for x, text, width in _cells(row, section):
                draw.text((x, y), fit_text(row_font, text, width), font=row_font,
                          fill=(*TEXT_PRIMARY, 255))
            y += _ROW_H
    return panel


class ReferencePointer:
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

    def showing(self, open_: bool) -> None:
        if open_ and not self.state.open:
            self.state = ReferenceState(open=True, page=_THE_FRONT_NOT_WHERE_READING_STOPPED)
        else:
            self.state = replace(self.state, open=open_)
