from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from app_support.file_channel import write_whole
from PIL import Image, ImageDraw
from shared_ui.icons_pil import pil_icon
from shared_ui.lettering import load_font, wrap_text
from shared_ui.palette import BG_BUTTON, BG_PRIMARY, TEXT_MUTED, TEXT_PRIMARY
from shared_ui.spacing import BUTTON_FILLING_ICON, BUTTON_RADIUS, BUTTON_SIZE, MARGIN_DIALOG
from shared_ui.type_scale import SIZE_SMALL, in_pixels

from fun_time.dashboard_layout import Rect

from .helper_processes import end_helper, start_helper

logger = logging.getLogger(__name__)

__all__ = [
    "NEXT_PAGE",
    "PAGES_FILENAME",
    "PREV_PAGE",
    "REFERENCE_WIDTH_DEG",
    "ReferencePage",
    "ReferencePages",
    "ReferencePagesHost",
    "ReferencePointer",
    "ReferenceState",
    "note_lines",
    "page_of",
    "paint_reference",
    "read_pages",
    "reference_actions",
    "reference_size",
    "scroll_limit",
    "write_pages",
]

REFERENCE_WIDTH_DEG = 54.0
PAGES_MODULE = "fun_time_vr.reference_pages"
PAGES_FOLDER = "reference_pages"
PAGES_FILENAME = "pages.json"

PREV_PAGE = "reference_prev"
NEXT_PAGE = "reference_next"

_PAD = MARGIN_DIALOG
_TALL_AS_IT_HAS_ALWAYS_HUNG_FOR_ITS_WIDTH = 1012 / 760
_ROW_PX = in_pixels(SIZE_SMALL)
_ROW_H = 17
_NOTE_LINES_AT_MOST = 3
_PANEL_ALPHA = 240
_THE_FRONT_NOT_WHERE_READING_STOPPED = 0
_THE_TOP_OF_THE_PAGE = 0


@dataclass(frozen=True)
class ReferencePage:
    title: str
    image: Image.Image
    title_ends_at: int
    note: str = ""


@dataclass(frozen=True)
class ReferencePages:
    pages: tuple[ReferencePage, ...]
    step_px: int


def write_pages(pages: ReferencePages, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    told = []
    for index, page in enumerate(pages.pages):
        name = f"page_{index}.png"
        page.image.save(folder / name)
        told.append({"title": page.title, "image": name, "title_ends_at": page.title_ends_at,
                     "note": page.note})
    write_whole(folder / PAGES_FILENAME, json.dumps({"pages": told, "step_px": pages.step_px}))


def read_pages(folder: Path) -> ReferencePages | None:
    try:
        told = json.loads((folder / PAGES_FILENAME).read_text(encoding="utf-8"))
        pages = tuple(
            ReferencePage(page["title"], Image.open(folder / page["image"]).convert("RGBA"),
                          page["title_ends_at"], page["note"])
            for page in told["pages"])
    except (OSError, ValueError, KeyError):
        return None
    return ReferencePages(pages, told["step_px"]) if pages else None


def reference_size(pages: ReferencePages) -> tuple[int, int]:
    width = pages.pages[0].image.width + 2 * _PAD
    return width, round(width * _TALL_AS_IT_HAS_ALWAYS_HUNG_FOR_ITS_WIDTH)


def reference_actions(pages: ReferencePages) -> dict[str, Rect]:
    right = reference_size(pages)[0] - _PAD - BUTTON_SIZE
    return {
        NEXT_PAGE: Rect(right, _PAD, BUTTON_SIZE, BUTTON_SIZE),
        PREV_PAGE: Rect(right - BUTTON_SIZE - 6, _PAD, BUTTON_SIZE, BUTTON_SIZE),
    }


def note_lines(note: str, pages: ReferencePages) -> list[str]:
    if not note:
        return []
    return wrap_text(load_font(_ROW_PX), " ".join(note.split()),
                     reference_size(pages)[0] - 2 * _PAD, max_lines=_NOTE_LINES_AT_MOST)


@dataclass(frozen=True)
class ReferenceState:
    open: bool = False
    page: int = 0
    scrolled: int = _THE_TOP_OF_THE_PAGE


def page_of(state: ReferenceState, pages: ReferencePages) -> int:
    return state.page % len(pages.pages)


def _head_height(page: ReferencePage) -> int:
    return _PAD + max(BUTTON_SIZE, page.title_ends_at) + _PAD // 2


def _rows_shown(page: ReferencePage, pages: ReferencePages) -> tuple[int, int]:
    top = _head_height(page) + _ROW_H * len(note_lines(page.note, pages))
    return top, reference_size(pages)[1] - _PAD - top


def scroll_limit(state: ReferenceState, pages: ReferencePages) -> int:
    page = pages.pages[page_of(state, pages)]
    _, shown = _rows_shown(page, pages)
    return max(0, page.image.height - page.title_ends_at - shown)


def paint_reference(state: ReferenceState, pages: ReferencePages) -> Image.Image:
    size = reference_size(pages)
    panel = Image.new("RGBA", size, (*BG_PRIMARY, _PANEL_ALPHA))
    page = pages.pages[page_of(state, pages)]
    width = page.image.width

    title = page.image.crop((0, 0, width, page.title_ends_at))
    title_top = _PAD + max(0, (BUTTON_SIZE - page.title_ends_at) // 2)
    panel.alpha_composite(_as_the_panel(title), (_PAD, title_top))
    _paint_the_controls(panel, pages)

    draw = ImageDraw.Draw(panel)
    y = _head_height(page)
    for line in note_lines(page.note, pages):
        draw.text((_PAD, y), line, font=load_font(_ROW_PX), fill=(*TEXT_MUTED, 255))
        y += _ROW_H

    top, shown = _rows_shown(page, pages)
    first = page.title_ends_at + min(state.scrolled, scroll_limit(state, pages))
    rows = page.image.crop((0, first, width, min(page.image.height, first + shown)))
    panel.alpha_composite(_as_the_panel(rows), (_PAD, top))
    return panel


def _as_the_panel(part: Image.Image) -> Image.Image:
    part = part.copy()
    part.putalpha(_PANEL_ALPHA)
    return part


def _paint_the_controls(panel: Image.Image, pages: ReferencePages) -> None:
    draw = ImageDraw.Draw(panel)
    for action, rect in reference_actions(pages).items():
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


class ReferencePointer:
    def __init__(self, state: ReferenceState | None = None) -> None:
        self.state = state or ReferenceState()

    def press(self, px: int, py: int, pages: ReferencePages) -> str | None:
        for action, rect in reference_actions(pages).items():
            if (rect.x <= px < rect.x + rect.width
                    and rect.y <= py < rect.y + rect.height):
                step = 1 if action == NEXT_PAGE else -1
                self.state = replace(self.state, page=self.state.page + step,
                                     scrolled=_THE_TOP_OF_THE_PAGE)
                return action
        return None

    def scroll(self, down_px: int, pages: ReferencePages) -> None:
        scrolled = max(0, min(self.state.scrolled + down_px, scroll_limit(self.state, pages)))
        self.state = replace(self.state, scrolled=scrolled)

    def showing(self, open_: bool) -> None:
        if open_ and not self.state.open:
            self.state = ReferenceState(open=True, page=_THE_FRONT_NOT_WHERE_READING_STOPPED)
        else:
            self.state = replace(self.state, open=open_)


class ReferencePagesHost:
    def __init__(
        self, *, state_dir: Path, python_exe: str = sys.executable,
        launch: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._folder = self._state_dir / PAGES_FOLDER
        self._python_exe = python_exe
        self._launch = launch
        self._process: subprocess.Popen | None = None
        self._pages: ReferencePages | None = None
        self._told_it_failed = False

    def pages(self) -> ReferencePages | None:
        if self._pages is not None:
            return self._pages
        if self._process is None:
            with contextlib.suppress(OSError):
                shutil.rmtree(self._folder)
            self._process = start_helper(
                "Reference", PAGES_MODULE,
                ["--state-dir", str(self._state_dir), "--out", str(self._folder)],
                python_exe=self._python_exe, launch=self._launch)
        self._pages = read_pages(self._folder)
        if self._pages is None and self._process.poll() not in (None, 0) and not self._told_it_failed:
            logger.warning("The reference's pages could not be drawn (exit code %s)",
                           self._process.poll())
            self._told_it_failed = True
        return self._pages

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            end_helper(self._process)
