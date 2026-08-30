"""Fun Time's own browser for the main player library.

The Windows file dialog this replaces browsed the library the way it sits on
disk — stage folders nested several deep, the same video filed under three of
them — so finding a video meant remembering how far it had got through the
pipeline.  This browses :mod:`fun_time.library_handles` instead: one tile per
video, whatever renditions it exists as, alphabetical, with a still off each.

The folder being shown is put up twice, side by side.  The grid of tiles is the
half you walk, ordered the way the library ranks itself — biggest source folder
first, cuts behind the videos they came out of — which is the order to browse
in and the wrong one to *find* in.  So the left sidebar is the other order: the
same folder as a plain list of names, A to Z, each letter's names under a
heading of that letter, for when the title is already in mind.

It runs as its own process (``python -m fun_time.library_browser``) because the
bridge that opens it has no Qt event loop, exactly as the native dialog did.
The pick leaves through the result file named on the command line; nothing
written means the browse was abandoned.
"""
from __future__ import annotations

import argparse
import configparser
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from app_support.subprocess_utils import hidden_subprocess_kwargs
from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QListWidget, QListWidgetItem, QWidget

from shared_ui.colors import BG_PRIMARY, BG_SECONDARY, BLUE, TEXT_MUTED, TEXT_PRIMARY
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_HEADING, SIZE_SMALL, make_font

from .library_handles import LibraryHandle, build_library_handles
from .library_tree import Folder, SubFolder, folder_at
from .process_identity import identified_python_exe
from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, cached_thumbnail, thumbnail_for

WINDOW_TITLE = "Fun Time Library"

# Tile size. Wide enough for a 16:9 still at the thumbnail cache's own longest
# edge, tall enough to carry two lines of title under it — library titles run
# long ("Jane Doe - Scene One"), and a name cut to one line is unrecognizable.
TILE_WIDTH = 200
TILE_HEIGHT = 168
ICON_WIDTH = 176
ICON_HEIGHT = 99

# How wide the alphabetical sidebar stands.  About a tile's width: enough for
# most of a library title before it elides, and little enough that the grid
# beside it still lays out several tiles across at the size a browse opens at.
SIDEBAR_WIDTH = 220

# What names with no letter to file under are headed by.  A library holds titles
# that open on a digit or a bracket, and each of those under a heading of its own
# first character would be an index with more headings in it than names.
NON_LETTER_HEADING = "#"

# What the tile that goes back up is called, at the two places it can appear.
UP_LABEL = "back"
UP_TO_LIBRARY = "all folders"

# The hairline between a folder tile's four stills, so they read as four
# pictures rather than one.
MONTAGE_GAP = 2

# How often the grid picks up thumbnails the background extractor has finished.
THUMBNAIL_POLL_MS = 150


class BrowseList(QListWidget):
    """A list in the browse — either of them, and Backspace goes back up from both.

    The key belongs to the browse rather than to one of its views: whichever
    half has the focus, it is the way out of a folder in every other file
    browser, and a sidebar that swallowed it would be a place the gesture
    silently stopped working.
    """

    def __init__(self, go_up: Callable[[], None]) -> None:
        super().__init__(None)
        self._go_up = go_up
        self.setFont(make_font(FONT_UI, SIZE_BODY))

    def keyPressEvent(self, event) -> None:  # Qt override
        """Backspace goes back up, the way it does in every other file browser."""
        if event.key() == Qt.Key.Key_Backspace:
            self._go_up()
            return
        super().keyPressEvent(event)


class LibraryGrid(BrowseList):
    """The tiles: one per folder or video in the folder being shown, pictured.

    Ordered as :func:`fun_time.library_handles.build_library_handles` ranked the
    library, which is what makes this the half you browse — the bulk of the
    library first, a folder's cuts behind its whole videos.  Finding a title you
    already know the name of is the sidebar's job instead.
    """

    def __init__(
        self,
        *,
        thumbnail_cache: str | Path,
        go_up: Callable[[], None],
        on_activate: Callable[[int], None],
    ) -> None:
        super().__init__(go_up)
        self._thumbnail_cache = Path(thumbnail_cache)

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(ICON_WIDTH, ICON_HEIGHT))
        self.setSpacing(6)
        self.setStyleSheet(
            f"QListWidget {{ background-color: {BG_PRIMARY.name()};"
            f" color: {TEXT_PRIMARY.name()}; border: none; }}"
            f" QListWidget::item {{ background-color: {BG_SECONDARY.name()};"
            " border-radius: 4px; padding: 4px; }"
            f" QListWidget::item:selected {{ background-color: {BLUE.name()}; }}"
        )

        # One entry per widget row: the handle a video tile plays, the SubFolder
        # a folder tile opens, or None for the tile that goes back up — rows and
        # handles do not line up once the grid is walkable.
        self.rows: list[LibraryHandle | SubFolder | None] = []
        # One signal covers both gestures: Qt emits itemActivated for Enter
        # AND at the end of a double-click, so nothing here needs to know
        # which of the two the user made.
        self.itemActivated.connect(lambda item: on_activate(self.row(item)))

        # Rows whose still is not cached yet are extracted off the event loop and
        # collected here; the timer below hands them to the grid.  A cold cache
        # would otherwise block the browse behind hundreds of HEVC decodes.
        self._extracted: queue.Queue[int] = queue.Queue()
        self._extractor: threading.Thread | None = None
        self._collect_timer = QTimer(self)
        self._collect_timer.timeout.connect(self._collect_thumbnails)

    def show_folder(self, folder: Folder) -> None:
        """Lay out *folder* — its sub-folder tiles, or the videos it holds."""
        self.clear()
        self.rows = []
        if folder.parent is not None:
            self._add_row(self._up_item(folder.parent), None)
        for child in folder.children:
            self._add_row(self._folder_item(child), child)
        for handle in folder.handles:
            self._add_row(self._tile_item(handle), handle)
        # The selection starts on the first thing you would open, not on the way
        # back — arrowing off the top of a folder is not what a browse is for.
        self.setCurrentRow(min(1 if folder.parent is not None else 0, self.count() - 1))
        self.start_thumbnail_extraction()

    def reveal(self, row: int) -> None:
        """Put the selection on *row* and scroll it up out of wherever it was.

        Centered rather than merely made visible: a jump out of the sidebar
        lands on a name the user cannot see yet, and one that arrives clinging
        to the bottom edge of the grid reads as not having moved.
        """
        item = self.item(row)
        if item is None:
            return
        self.setCurrentRow(row)
        self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _add_row(self, item: QListWidgetItem, what: LibraryHandle | SubFolder | None) -> None:
        self.rows.append(what)
        self.addItem(item)

    def _tile_item(self, handle: LibraryHandle) -> QListWidgetItem:
        return self._pictured_item(handle.title, handle.preview)

    def _folder_item(self, child: SubFolder) -> QListWidgetItem:
        """A folder tile: its name, how much is in it, and stills from inside."""
        item = QListWidgetItem(f"{child.name}  ({child.count})")
        item.setSizeHint(QSize(TILE_WIDTH, TILE_HEIGHT))
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        stills = [
            cached for cached in (
                cached_thumbnail(preview, self._thumbnail_cache) for preview in child.previews
            ) if cached is not None
        ]
        if stills:
            item.setIcon(montage_icon(stills))
        return item

    def _up_item(self, parent: tuple[str, ...]) -> QListWidgetItem:
        """The way back — first tile, so it is where the eye and the arrows start."""
        item = QListWidgetItem(UP_LABEL if parent else UP_TO_LIBRARY)
        item.setSizeHint(QSize(TILE_WIDTH, TILE_HEIGHT))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
        item.setForeground(TEXT_MUTED)
        return item

    def _pictured_item(self, label: str, preview: str) -> QListWidgetItem:
        item = QListWidgetItem(label)
        item.setSizeHint(QSize(TILE_WIDTH, TILE_HEIGHT))
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        cached = cached_thumbnail(preview, self._thumbnail_cache)
        if cached is not None:
            item.setIcon(fitted_icon(cached))
        return item

    def start_thumbnail_extraction(self) -> None:
        """Fill in the stills the cache did not already have, in the background."""
        pending = rows_needing_stills(self.rows, self._thumbnail_cache)
        if not pending or (self._extractor is not None and self._extractor.is_alive()):
            return
        self._extractor = threading.Thread(
            target=self._extract, args=(pending, tuple(self.rows)), daemon=True,
            name="library-thumbnails",
        )
        self._extractor.start()
        self._collect_timer.start(THUMBNAIL_POLL_MS)

    def _extract(self, rows: Sequence[int], showing: Sequence[object]) -> None:
        # The rows are captured, not read live: opening a folder mid-extraction
        # replaces them, and a still must never land on whatever row now sits at
        # that index in another folder.
        for row in rows:
            for preview in previews_of(showing[row]):
                thumbnail_for(preview, self._thumbnail_cache)
            self._extracted.put(row)

    def _collect_thumbnails(self) -> None:
        while True:
            try:
                row = self._extracted.get_nowait()
            except queue.Empty:
                break
            item = self.item(row)
            what = self.rows[row] if row < len(self.rows) else None
            if item is None or what is None:
                continue
            stills = [
                cached for cached in (
                    cached_thumbnail(preview, self._thumbnail_cache)
                    for preview in previews_of(what)
                ) if cached is not None
            ]
            if stills:
                item.setIcon(montage_icon(stills) if isinstance(what, SubFolder)
                             else fitted_icon(stills[0]))
        if self._extractor is not None and not self._extractor.is_alive():
            self._collect_timer.stop()


class FolderIndex(BrowseList):
    """The folder as a plain list of names, A to Z, under a heading per letter.

    The grid beside this is in the library's own ranking, which is the order to
    look *through* a folder in and no help at all when the title is already in
    mind: an alphabetical walk across it is a walk across a wrapped grid of
    stills.  So the same folder goes up again here as text alone, sorted by
    name, with the letter each group files under standing over it.

    Choosing a name moves the grid to it.  The grid never moves this in return,
    deliberately: an index that re-scrolled itself every time the grid's
    selection changed would slide out from under the walk down it that caused
    the change.
    """

    def __init__(
        self,
        *,
        go_up: Callable[[], None],
        on_reveal: Callable[[int], None],
        on_activate: Callable[[int], None],
    ) -> None:
        super().__init__(go_up)
        self._on_reveal = on_reveal
        self._on_activate = on_activate
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setStyleSheet(
            f"QListWidget {{ background-color: {BG_SECONDARY.name()};"
            f" color: {TEXT_PRIMARY.name()};"
            f" border: none; border-right: 1px solid {BG_PRIMARY.name()}; }}"
            " QListWidget::item { padding: 2px 6px; }"
            f" QListWidget::item:selected {{ background-color: {BLUE.name()}; }}"
            # The letter headings, which are the only rows here that are disabled
            # — they stand over the names rather than reading as more of them.
            f" QListWidget::item:disabled {{ color: {TEXT_MUTED.name()}; }}"
        )

        # One entry per widget row: which grid row that line stands for, or None
        # for a letter heading, which stands for nothing you can open.
        self.grid_rows: list[int | None] = []
        self.currentItemChanged.connect(self._reveal)
        self.itemActivated.connect(self._activate)

    def show_rows(self, rows: Sequence[object]) -> None:
        """List *rows* — the grid's, in its order — alphabetically under headings."""
        # Cleared before the widget is, so the currentItemChanged that clearing
        # fires cannot be answered against a mapping for the folder just left.
        self.grid_rows = []
        self.clear()
        for line in alphabetical_index(rows):
            self._add_line(
                self._heading_item(line.label) if line.is_heading
                else self._name_item(line.label),
                line.row,
            )
        self._hold_to_the_sidebars_width()

    def _add_line(self, item: QListWidgetItem, row: int | None) -> None:
        self.grid_rows.append(row)
        self.addItem(item)

    def _hold_to_the_sidebars_width(self) -> None:
        """Hand every line a width Qt will stretch, rather than lay the list out to.

        A list view sizes its rows to the widest size hint it was given, and a
        library title runs several times the width of this one: left alone the
        rows grow the list sideways and hang a horizontal scrollbar under an
        index meant to be read straight down, with every name cut off at the
        edge rather than elided.  A hint narrower than the viewport is stretched
        to fill it instead, so the names elide at the sidebar's own edge however
        much of it the vertical scrollbar takes.  The height is asked for first,
        because that one is the delegate's to decide.
        """
        for row in range(self.count()):
            self.item(row).setSizeHint(QSize(1, self.sizeHintForRow(row)))

    def _heading_item(self, letter: str) -> QListWidgetItem:
        """A letter standing over its names — and never landed on.

        Flagless, so it is neither selectable nor enabled: Qt's own arrow
        navigation and type-ahead both step over a disabled row, which is what
        keeps a walk down the index a walk down its names.  Being the only
        disabled rows here is also what the stylesheet mutes them by.
        """
        item = QListWidgetItem(letter)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setFont(make_font(FONT_UI, SIZE_SMALL, bold=True))
        return item

    def _name_item(self, name: str) -> QListWidgetItem:
        # Named again as its own tooltip: the sidebar is a fixed width and
        # library titles run past it, so the elided ones are still readable.
        item = QListWidgetItem(name)
        item.setToolTip(name)
        return item

    def _grid_row(self, item: QListWidgetItem | None) -> int | None:
        if item is None:
            return None
        row = self.row(item)
        return self.grid_rows[row] if 0 <= row < len(self.grid_rows) else None

    def _reveal(self, item: QListWidgetItem | None, _previous: object = None) -> None:
        row = self._grid_row(item)
        if row is not None:
            self._on_reveal(row)

    def _activate(self, item: QListWidgetItem) -> None:
        row = self._grid_row(item)
        if row is not None:
            self._on_activate(row)


class LibraryBrowserWindow(QWidget):
    """A folder shown two ways at once: tiles you walk, and its names A to Z.

    The folders are the library's own divisions, never the pipeline's — see
    :mod:`fun_time.library_tree`.  Opening the last one hands over every video
    under it at once, so no processing stage is ever a step.

    Picking is deliberately the only way out that reports anything: *on_pick*
    is called with the handle's canonical video, and closing the window without
    picking says nothing, which is what abandoning a browse means.

    Escape is deliberately NOT bound.  It belongs to OmniPause, whose AHK hotkey
    is suspend-exempt on purpose (it is the way *out* of a pause), so the press
    never reaches this window however it is handled here; the window's own close
    button is what abandons a browse.  Every other key does reach it, because
    the bridge suspends the hotkeys for the browse's duration.
    """

    def __init__(
        self,
        handles: Sequence[LibraryHandle],
        *,
        thumbnail_cache: str | Path,
        on_pick: Callable[[str], None],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(None)
        # A Tool window, which on Windows means no taskbar button: a browse is
        # something you open and dismiss, not a program that is running.  Left
        # a plain window it earns its own indicator, and — declaring no identity
        # of its own — Windows hangs that off whatever app it can pair it with.
        self.setWindowFlags(Qt.WindowType.Tool)
        self._handles = tuple(handles)
        self._on_pick = on_pick
        self._on_close = on_close
        self._path: tuple[str, ...] = ()

        self.grid = LibraryGrid(
            thumbnail_cache=thumbnail_cache, go_up=self.go_up, on_activate=self._activate,
        )
        self.index = FolderIndex(
            go_up=self.go_up, on_reveal=self.grid.reveal, on_activate=self._activate,
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.index)
        layout.addWidget(self.grid, 1)
        # Painted through the palette rather than a stylesheet: a QWidget
        # subclass draws neither its own background nor a stylesheet's unless it
        # paints one, and this is the sliver the two lists do not cover.
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, BG_PRIMARY)
        self.setPalette(palette)

        self.open_folder(())
        # The grid takes the focus, though the sidebar is first in the layout and
        # would otherwise have it: the arrows and the type-ahead are the way the
        # browse is driven, and both belong on the tiles.
        self.grid.setFocus()

    def open_folder(self, path: Sequence[str]) -> None:
        """Show *path* in both halves: its folder tiles, or the videos it holds."""
        folder = folder_at(self._handles, path)
        self._path = folder.path
        self.setWindowTitle(f"{WINDOW_TITLE} — {folder.title}" if folder.title else WINDOW_TITLE)
        self.grid.show_folder(folder)
        self.index.show_rows(self.grid.rows)

    def go_up(self) -> None:
        """Leave the folder being shown for the one that holds it."""
        if self._path:
            self.open_folder(self._path[:-1])

    def closeEvent(self, event) -> None:  # Qt override
        """Tell the process the browse is over — closing this window cannot.

        Qt quits an app when its last window closes, but a Tool window is not
        counted as one (it is chrome for another window, by Qt's reckoning).  The
        browser has only Tool windows, so nothing would ever end its event loop:
        the picked video would sit in the result file with the bridge still
        blocked on a process that had nothing left to do.
        """
        super().closeEvent(event)
        if self._on_close is not None:
            self._on_close()

    def _activate(self, row: int) -> None:
        """Open a folder, go back up, or play a video — whatever the row is."""
        what = self.grid.rows[row]
        if what is None:
            self.go_up()
        elif isinstance(what, SubFolder):
            self.open_folder((*self._path, what.name))
        else:
            self._on_pick(what.video)
            self.close()


@dataclass(frozen=True)
class IndexLine:
    """One line of the sidebar: a letter heading, or a name that opens a grid row."""

    label: str
    row: int | None = None

    @property
    def is_heading(self) -> bool:
        """Whether this line names a group rather than something in one."""
        return self.row is None


def name_of(what: LibraryHandle | SubFolder | None) -> str:
    """What a row is called — a video's title, or a folder's name."""
    return what.display_name if what is not None else ""


def initial_letter(name: str) -> str:
    """The heading *name* files under: its first letter, or ``#`` for the rest."""
    first = name.strip()[:1].upper()
    return first if first.isalpha() else NON_LETTER_HEADING


def alphabetical_index(rows: Sequence[object]) -> list[IndexLine]:
    """*rows* listed A to Z, each letter's names beneath a heading of that letter.

    Rows carry their grid position with them rather than being re-counted, since
    the two orders disagree by design — that disagreement is the whole reason
    the sidebar exists.  Sorting is case-insensitive first and exact second,
    exactly as :func:`fun_time.library_handles.build_library_handles` ranks
    within a folder, so a folder already alphabetical comes up in the order it
    is in.  The way back is left out: it is not something the folder holds.
    """
    named = sorted(
        ((name_of(what), row) for row, what in enumerate(rows) if what is not None),
        key=lambda named_row: (named_row[0].casefold(), named_row[0]),
    )
    lines: list[IndexLine] = []
    heading = ""
    for name, row in named:
        letter = initial_letter(name)
        if letter != heading:
            heading = letter
            lines.append(IndexLine(letter))
        lines.append(IndexLine(name, row))
    return lines


def fitted_icon(still: str | Path) -> QIcon:
    """*still* grown to meet a tile's edge, with its proportions untouched.

    Qt would otherwise draw the icon at exactly the icon size, stretching a
    picture that is not the tile's shape — and a library holds both tall videos
    and wide ones, so one of the two axes always has room to spare.  Scaling here
    rather than leaving it to the view also grows a still that is *smaller* than
    the tile: the cache caps its longest edge below the tile's, so an unscaled
    one sits in a corner of the space it was given.
    """
    return QIcon(_fitted(still, ICON_WIDTH, ICON_HEIGHT))


def _fitted(still: str | Path, width: int, height: int) -> QPixmap:
    return QPixmap(str(still)).scaled(
        width, height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def montage_icon(stills: Sequence[str | Path]) -> QIcon:
    """*stills* laid out two by two across one tile, each keeping its shape.

    A folder of hundreds said almost nothing when it was drawn with a single
    still, so it is drawn with four of its videos instead.  One still gets the
    whole tile — there is nothing to quarter it around — and a folder that holds
    two or three leaves the spare cells empty rather than repeating itself.
    """
    if len(stills) == 1:
        return fitted_icon(stills[0])
    cell_width = (ICON_WIDTH - MONTAGE_GAP) // 2
    cell_height = (ICON_HEIGHT - MONTAGE_GAP) // 2
    canvas = QPixmap(ICON_WIDTH, ICON_HEIGHT)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        for index, still in enumerate(stills[:4]):
            picture = _fitted(still, cell_width, cell_height)
            left = (index % 2) * (cell_width + MONTAGE_GAP)
            top = (index // 2) * (cell_height + MONTAGE_GAP)
            painter.drawPixmap(
                left + (cell_width - picture.width()) // 2,
                top + (cell_height - picture.height()) // 2,
                picture,
            )
    finally:
        painter.end()
    return QIcon(canvas)


def previews_of(what: LibraryHandle | SubFolder | None) -> tuple[str, ...]:
    """The videos a row is pictured with — one for a video, up to four for a folder."""
    return what.previews if what is not None else ()


def rows_needing_stills(rows: Sequence[object], thumbnail_cache: str | Path) -> list[int]:
    """Which rows still need a still extracted — the cache misses, in order.

    The go-back row pictures nothing and is skipped; a folder row counts as a
    miss while any of its four is missing, so its tile completes.
    """
    return [
        row
        for row, what in enumerate(rows)
        if any(
            cached_thumbnail(preview, thumbnail_cache) is None
            for preview in previews_of(what)
        )
    ]


PICK_FILENAME = "library_browser_pick.txt"


def pick_file_for(manifest_path: str | Path) -> Path:
    """Where a browse leaves the video it picked, beside the session's state."""
    return Path(manifest_path).parent / PICK_FILENAME


def browse_library(
    manifest_path: str | Path,
    python_exe: str,
    *,
    over: tuple[int, int, int, int] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> str | None:
    """Browse the library and return the video picked, or None if none was.

    Blocks for the length of the browse, as the file dialog before it did — the
    caller is a dispatch-loop thread, and the browser is a window of its own
    because the bridge process has no Qt event loop to host one in.
    """
    pick_file = pick_file_for(manifest_path)
    # Last browse's pick would otherwise stand in for this one's, and an
    # abandoned browse would jump the session to a video nobody chose.
    pick_file.unlink(missing_ok=True)

    command = [
        identified_python_exe(python_exe, "LibraryBrowser"),
        "-m", "fun_time.library_browser", str(manifest_path), str(pick_file),
    ]
    if over is not None:
        x, y, width, height = over
        command += ["--x", str(x), "--y", str(y), "--width", str(width), "--height", str(height)]
    runner(command, **hidden_subprocess_kwargs())

    try:
        return pick_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


@dataclass(frozen=True)
class BrowserConfig:
    """Where the browser reads the library, its families, and its stills from."""

    sources: str
    metadata_root: Path | None
    thumbnail_cache: Path


def load_browser_config(manifest_path: str | Path) -> BrowserConfig:
    """Read the browser's inputs out of the bridge's launch manifest.

    The same manifest every other child process reads, so the browser can never
    disagree with the session about which folders are the main library.
    """
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(manifest_path, encoding="utf-8")
    metadata_root = parser.get("regen", "metadata_root", fallback="")
    return BrowserConfig(
        sources=parser.get("media", "nau_library_sources", fallback=""),
        metadata_root=Path(metadata_root) if metadata_root else None,
        thumbnail_cache=Path(manifest_path).parent / THUMBNAIL_CACHE_DIRNAME,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse the Fun Time main library")
    parser.add_argument("manifest_path", help="Path to the Windows bridge launch manifest")
    parser.add_argument("result_file", help="Where to write the chosen video path")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from PyQt6.QtWidgets import QApplication

    args = parse_args(argv)
    # Claim Fun Time's identity before any window exists, so the browse is never
    # mistaken for an unrelated app's window (see the Tool flag above).
    from .win32_taskbar import APP_USER_MODEL_ID, set_app_user_model_id
    try:
        set_app_user_model_id(APP_USER_MODEL_ID)
    except OSError:
        pass  # Non-fatal — taskbar identity just falls back to the default

    app = QApplication.instance() or QApplication([])

    config = load_browser_config(args.manifest_path)
    result_file = Path(args.result_file)
    window = LibraryBrowserWindow(
        build_library_handles(config.sources, config.metadata_root),
        thumbnail_cache=config.thumbnail_cache,
        on_pick=lambda video: result_file.write_text(video, encoding="utf-8"),
        on_close=app.quit,
    )
    if None not in {args.x, args.y, args.width, args.height}:
        window.setGeometry(args.x, args.y, args.width, args.height)
    window.show()
    window.activateWindow()
    window.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
