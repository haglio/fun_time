from __future__ import annotations

import queue
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from app_support.threading_utils import start_daemon_thread
from PIL import Image, ImageDraw, ImageOps
from shared_ui.icons_pil import glyph_image
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    MAGENTA,
    TEXT_MUTED,
    TEXT_PRIMARY,
    hovered,
)
from shared_ui.spacing import (
    BUTTON_GAP,
    BUTTON_GROUP_GAP,
    BUTTON_ICON,
    BUTTON_RADIUS,
    BUTTON_SIZE,
)

from fun_time.dashboard_layout import Rect
from fun_time.library_handles import LibraryHandle, handle_for
from fun_time.library_tree import Folder, SubFolder, folder_at, folder_of
from fun_time.thumbnail_cache import cached_thumbnail, prewarm_thumbnails

from .lettering import fit_text, load_font

LIBRARY_WIDTH_PX = 976

_COLUMNS = 5
_ROWS = 3
_PER_PAGE = _COLUMNS * _ROWS
_PAD = 12
_GAP = 8
_TILE_W = (LIBRARY_WIDTH_PX - 2 * _PAD - (_COLUMNS - 1) * _GAP) // _COLUMNS
_TILE_H = 144
_BACK_W = 64
_BODY_PX = 13
_TITLE_PX = 16
_STILL_INSET = 4
_STILL_H = 99
_MONTAGE_GAP = 2
_LABEL_LINE_H = 16

_READING = "Reading the library…"
_BACK = "Back"
_LIBRARY_TITLE = "Library"

CLOSE = "library_close"
GO_UP = "library_up"
NEXT_PAGE = "library_next"
PREV_PAGE = "library_prev"

_MARKS = {PREV_PAGE: "chevron_left", NEXT_PAGE: "chevron_right", CLOSE: "cross"}


def library_actions() -> dict[str, Rect]:
    close_x = LIBRARY_WIDTH_PX - _PAD - BUTTON_SIZE
    next_x = close_x - BUTTON_GROUP_GAP - BUTTON_SIZE
    return {
        GO_UP: Rect(_PAD, _PAD, _BACK_W, BUTTON_SIZE),
        PREV_PAGE: Rect(next_x - BUTTON_GAP - BUTTON_SIZE, _PAD, BUTTON_SIZE, BUTTON_SIZE),
        NEXT_PAGE: Rect(next_x, _PAD, BUTTON_SIZE, BUTTON_SIZE),
        CLOSE: Rect(close_x, _PAD, BUTTON_SIZE, BUTTON_SIZE),
    }


def tile_rects() -> tuple[Rect, ...]:
    top = _PAD + BUTTON_SIZE + _PAD
    return tuple(
        Rect(_PAD + column * (_TILE_W + _GAP), top + row * (_TILE_H + _GAP), _TILE_W, _TILE_H)
        for row in range(_ROWS)
        for column in range(_COLUMNS)
    )


def library_height() -> int:
    last = tile_rects()[-1]
    return last.y + last.height + _PAD


def _inside(rect: Rect, px: int, py: int) -> bool:
    return rect.x <= px < rect.x + rect.width and rect.y <= py < rect.y + rect.height


class LibraryBrowse:
    def __init__(
        self, *, play: Callable[[str], None], close: Callable[[], None],
        playing: Callable[[], str],
    ) -> None:
        self._play = play
        self._close = close
        self._playing = playing
        self.handles: tuple[LibraryHandle, ...] | None = None
        self.open = False
        self.lit: LibraryHandle | None = None
        self.folder: Folder = folder_at((), ())
        self.page = 0
        self._opened_during = ""
        self._put_away = False

    def stocked(self, handles: Sequence[LibraryHandle]) -> None:
        self.handles = tuple(handles)
        if self.open:
            self._open_on_what_was_playing()
        else:
            self._show(())

    def showing(self, open_: bool) -> None:
        if not open_:
            self._put_away = False
        opening = open_ and not self._put_away and not self.open
        self.open = open_ and not self._put_away
        if opening:
            self._opened_during = self._playing()
            self._open_on_what_was_playing()

    @property
    def tiles(self) -> tuple[SubFolder | LibraryHandle, ...]:
        start = self.page * _PER_PAGE
        return self._every_tile()[start:start + _PER_PAGE]

    @property
    def pages(self) -> int:
        return max(1, -(-len(self._every_tile()) // _PER_PAGE))

    def press(self, px: int, py: int) -> None:
        for action, rect in library_actions().items():
            if _inside(rect, px, py):
                self._act(action)
                return
        tiles = self.tiles
        for index, rect in enumerate(tile_rects()):
            if _inside(rect, px, py) and index < len(tiles):
                tile = tiles[index]
                if isinstance(tile, SubFolder):
                    self._show((*self.folder.path, tile.name))
                else:
                    self._play(tile.video)
                    self._put_it_away()
                return

    def _every_tile(self) -> tuple[SubFolder | LibraryHandle, ...]:
        return self.folder.children + self.folder.handles

    def _show(self, path: Sequence[str]) -> None:
        self.folder = folder_at(self.handles or (), path)
        self.page = 0

    def _open_on_what_was_playing(self) -> None:
        self.lit = handle_for(self.handles or (), self._opened_during)
        if self.lit is None:
            self._show(())
            return
        self._show(folder_of(self.lit))
        self.page = self._every_tile().index(self.lit) // _PER_PAGE

    def _act(self, action: str) -> None:
        if action == CLOSE:
            self._put_it_away()
        elif action == GO_UP and self.folder.parent is not None:
            left = self.folder.path[-1]
            self._show(self.folder.parent)
            self.page = [child.name for child in self.folder.children].index(left) // _PER_PAGE
        elif action == NEXT_PAGE:
            self.page = min(self.pages - 1, self.page + 1)
        elif action == PREV_PAGE:
            self.page = max(0, self.page - 1)

    def _put_it_away(self) -> None:
        self._close()
        self._put_away = True
        self.open = False


class LibraryShelf:
    def __init__(self, read: Callable[[], Sequence[LibraryHandle]]) -> None:
        self.handles: tuple[LibraryHandle, ...] | None = None
        start_daemon_thread(target=self._shelve, args=(read,), name="library-read")

    def _shelve(self, read: Callable[[], Sequence[LibraryHandle]]) -> None:
        try:
            self.handles = tuple(read())
        except OSError:
            self.handles = ()


def cached_or_extracted(preview: str, cache_dir: Path) -> Path | None:
    cached = cached_thumbnail(preview, cache_dir)
    if cached is None:
        prewarm_thumbnails([preview], cache_dir)
        cached = cached_thumbnail(preview, cache_dir)
    return cached


class LibraryStills:
    def __init__(self, cache_dir: Path, *, fetch: Callable[[str, Path], Path | None]) -> None:
        self._cache_dir = Path(cache_dir)
        self._fetch = fetch
        self._images: dict[str, Image.Image] = {}
        self._asked: set[str] = set()
        self._wanted: queue.LifoQueue[str | None] = queue.LifoQueue()
        self._arrived: queue.SimpleQueue[tuple[str, Image.Image]] = queue.SimpleQueue()
        self._fetching = False

    def want(self, previews: Iterable[str]) -> None:
        for preview in reversed(list(previews)):
            if preview not in self._asked:
                self._asked.add(preview)
                self._wanted.put(preview)
        if not self._fetching:
            self._fetching = True
            start_daemon_thread(target=self._fetch_until_closed, name="library-stills")

    def collect(self) -> bool:
        arrived = False
        while True:
            try:
                preview, image = self._arrived.get_nowait()
            except queue.Empty:
                return arrived
            self._images[preview] = image
            arrived = True

    def image(self, preview: str) -> Image.Image | None:
        return self._images.get(preview)

    def close(self) -> None:
        self._wanted.put(None)

    def _fetch_until_closed(self) -> None:
        while (preview := self._wanted.get()) is not None:
            try:
                path = self._fetch(preview, self._cache_dir)
                if path is None:
                    continue
                with Image.open(path) as opened:
                    still = opened.convert("RGB")
            except OSError:
                continue
            self._arrived.put((preview, still))


def paint_library(
    browse: LibraryBrowse, stills, hover: tuple[int, int] | None = None,
) -> Image.Image:
    panel = Image.new("RGBA", (LIBRARY_WIDTH_PX, library_height()), (*BG_PRIMARY, 240))
    draw = ImageDraw.Draw(panel)
    font = load_font(_BODY_PX)
    actions = library_actions()
    back, prev = actions[GO_UP], actions[PREV_PAGE]
    if browse.folder.parent is not None:
        _slab(draw, back, _lifted(BG_BUTTON, back, hover))
        _line_in(draw, back, _BACK, font, TEXT_PRIMARY)
    counter = f"{browse.page + 1} / {browse.pages}"
    counter_w = round(font.getlength(counter))
    counter_x = prev.x - BUTTON_GROUP_GAP - counter_w
    _line_in(draw, Rect(counter_x, _PAD, counter_w, BUTTON_SIZE), counter, font, TEXT_MUTED,
             left=True)
    title_x = back.x + back.width + BUTTON_GROUP_GAP
    title_font = load_font(_TITLE_PX)
    title = fit_text(title_font, browse.folder.title or _LIBRARY_TITLE,
                     counter_x - BUTTON_GROUP_GAP - title_x)
    _line_in(draw, Rect(title_x, _PAD, counter_x - title_x, BUTTON_SIZE), title, title_font,
             MAGENTA, left=True)
    for action, mark in _MARKS.items():
        rect = actions[action]
        _slab(draw, rect, _lifted(BG_BUTTON, rect, hover))
        panel.alpha_composite(
            glyph_image(mark, BUTTON_ICON, TEXT_PRIMARY),
            (rect.x + (rect.width - BUTTON_ICON) // 2, rect.y + (rect.height - BUTTON_ICON) // 2),
        )
    if browse.handles is None:
        first = tile_rects()[0]
        draw.text((first.x, first.y), _READING, font=font, fill=(*TEXT_MUTED, 255))
    for tile, rect in zip(browse.tiles, tile_rects(), strict=False):
        _slab(draw, rect, _lifted(BLUE if tile == browse.lit else BG_SECONDARY, rect, hover))
        pictures = [still for preview in tile.previews
                    if (still := stills.image(preview)) is not None]
        _picture(panel, pictures, _still_box(rect))
        inner = rect.width - 2 * _STILL_INSET
        for line_index, line in enumerate(wrap_label(font, label_of(tile), inner)):
            top = rect.y + 2 * _STILL_INSET + _STILL_H + line_index * _LABEL_LINE_H
            draw.text((rect.x + _STILL_INSET, top), line, font=font, fill=(*TEXT_PRIMARY, 255))
    return panel


def _lifted(ground, rect: Rect, hover: tuple[int, int] | None):
    return hovered(ground) if hover is not None and _inside(rect, *hover) else ground


def _slab(draw: ImageDraw.ImageDraw, rect: Rect, ground) -> None:
    draw.rounded_rectangle(
        (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
        radius=BUTTON_RADIUS, fill=(*ground, 255),
    )


def _line_in(
    draw: ImageDraw.ImageDraw, rect: Rect, text: str, font, ink, *, left: bool = False,
) -> None:
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    x = rect.x - x0 if left else rect.x + (rect.width - (x1 - x0)) // 2 - x0
    draw.text((x, rect.y + (rect.height - (y1 - y0)) // 2 - y0), text, font=font,
              fill=(*ink, 255))


def label_of(tile: SubFolder | LibraryHandle) -> str:
    return f"{tile.name}  ({tile.count})" if isinstance(tile, SubFolder) else tile.title


def wrap_label(font, text: str, width: int) -> list[str]:
    words = text.split()
    first = ""
    while words and font.getlength(f"{first} {words[0]}".strip()) <= width:
        first = f"{first} {words.pop(0)}".strip()
    if not first:
        return [fit_text(font, text, width)]
    rest = " ".join(words)
    return [first, fit_text(font, rest, width)] if rest else [first]


def _picture(panel: Image.Image, pictures: Sequence[Image.Image], box: Rect) -> None:
    if len(pictures) == 1:
        _paste_centered(panel, pictures[0], box)
        return
    cell_w = (box.width - _MONTAGE_GAP) // 2
    cell_h = (box.height - _MONTAGE_GAP) // 2
    for index, picture in enumerate(pictures[:4]):
        _paste_centered(panel, picture, Rect(
            box.x + (index % 2) * (cell_w + _MONTAGE_GAP),
            box.y + (index // 2) * (cell_h + _MONTAGE_GAP), cell_w, cell_h))


def _still_box(tile: Rect) -> Rect:
    return Rect(tile.x + _STILL_INSET, tile.y + _STILL_INSET,
                tile.width - 2 * _STILL_INSET, _STILL_H)


def _paste_centered(panel: Image.Image, picture: Image.Image, box: Rect) -> None:
    fitted = ImageOps.contain(picture, (box.width, box.height)).convert("RGBA")
    panel.paste(fitted, (box.x + (box.width - fitted.width) // 2,
                         box.y + (box.height - fitted.height) // 2))
