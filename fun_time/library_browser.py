"""Fun Time's own browser for the main player library.

The Windows file dialog this replaces browsed the library the way it sits on
disk — stage folders nested several deep, the same video filed under three of
them — so finding a video meant remembering how far it had got through the
pipeline.  This browses :mod:`fun_time.library_handles` instead: one tile per
video, whatever renditions it exists as, alphabetical, with a still off each.

The folder being shown is put up twice, side by side.  The grid of tiles is the
half you walk, ordered the way the library ranks itself — biggest source folder
first, cuts after the videos they came out of — which is the order to browse
in and the wrong one to *find* in.  So the left sidebar is the other order: the
letters A to Z, each opening onto its names when clicked.

It runs as its own process (``python -m fun_time.library_browser``) because the
bridge that opens it has no Qt event loop, exactly as the native dialog did.
The pick leaves through the result file named on the command line; nothing
written means the browse was abandoned.
"""
from __future__ import annotations

import argparse
import configparser
import html
import queue
import string
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs
from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shared_ui.chrome import family_stylesheet
from shared_ui.colors import (
    BG_BUTTON,
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    BLUE_LIGHT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    hovered,
)
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_HEADING, make_font
from shared_ui.icons import glyph_icon
from shared_ui.spacing import BUTTON_ICON, BUTTON_RADIUS, BUTTON_SIZE, MARGIN_STANDARD

from .library_handles import LibraryHandle, handle_for, handles_by_shape
from .library_tree import Folder, SubFolder, folder_at, folder_of
from .process_identity import NAMER
from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, cached_thumbnail, thumbnail_for
from .win32 import force_foreground_window

WINDOW_TITLE = "Fun Time Library"
TOP_LEVEL_NAME = "Library"

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

OPEN_MARK = "▾"
CLOSED_MARK = "▸"

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
    library first, a folder's cuts after its whole videos.  Finding a title you
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
        # would otherwise block the browse on hundreds of HEVC decodes.
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
        to the lower edge of the grid reads as not having moved.
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
    """The folder's letters, A to Z, each opening onto its names when clicked.

    Choosing a name moves the grid to it, and so does clicking a letter.  The
    grid moves this back only when a browse opens (:meth:`reveal`): an index
    that re-scrolled on every change of the grid's selection would slide out
    from under the walk that caused it.
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
        )

        # One line per widget row: a letter, or a name and the grid row it opens.
        self.lines: list[IndexLine] = []
        self._open_heading: int | None = None
        self.currentItemChanged.connect(self._reveal)
        self.itemActivated.connect(self._activate)

    def show_rows(self, rows: Sequence[object]) -> None:
        """List *rows* — the grid's, in its order — alphabetically under headings."""
        # Cleared before the widget is, so the currentItemChanged that clearing
        # fires cannot be answered against a mapping for the folder just left.
        self.lines = []
        self.clear()
        for line in alphabetical_index(rows):
            self._add_line(line)
        self._hold_to_the_sidebars_width()
        self._open_only(None)

    def _add_line(self, line: IndexLine) -> None:
        self.lines.append(line)
        self.addItem(
            self._heading_item(line.label) if line.is_heading else self._name_item(line.label)
        )

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
        item = QListWidgetItem(letter)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
        return item

    def _name_item(self, name: str) -> QListWidgetItem:
        # Named again as its own tooltip: the sidebar is a fixed width and
        # library titles run past it, so the elided ones are still readable.
        item = QListWidgetItem(name)
        item.setToolTip(name)
        return item

    def reveal(self, grid_row: int) -> None:
        row = next((r for r, line in enumerate(self.lines) if line.row == grid_row), None)
        if row is None:
            return
        self._open_only(self._heading_above(row))
        self.setCurrentRow(row)
        self.scrollToItem(self.item(row), QAbstractItemView.ScrollHint.PositionAtCenter)

    def _heading_above(self, row: int) -> int:
        return next(r for r in range(row, -1, -1) if self.lines[r].is_heading)

    def mousePressEvent(self, event) -> None:  # Qt override
        # A disabled row gets no click signal, so a heading answers one here.
        item = self.itemAt(event.position().toPoint())
        row = self.row(item) if item is not None else -1
        if not (0 <= row < len(self.lines)) or not self.lines[row].is_heading:
            super().mousePressEvent(event)
            return
        if not self._has_names(row):
            return
        if row == self._open_heading:
            self._open_only(None)
            return
        self._open_only(row)
        self.setCurrentRow(row + 1)
        self._reveal(self.item(row + 1))  # silent when it is already current

    def _open_only(self, heading_row: int | None) -> None:
        self._open_heading = heading_row
        heading = None
        for row, line in enumerate(self.lines):
            if line.is_heading:
                heading = row
                self.item(row).setText(self._letter_label(row))
                self.item(row).setForeground(TEXT_PRIMARY if self._has_names(row) else TEXT_MUTED)
            else:
                self.setRowHidden(row, heading != heading_row)

    def _letter_label(self, row: int) -> str:
        letter = self.lines[row].label
        if row == self._open_heading:
            return f"{letter} {OPEN_MARK}"
        if self._has_names(row):
            return f"{letter} {CLOSED_MARK}"
        return letter

    def _has_names(self, row: int) -> bool:
        return row + 1 < len(self.lines) and not self.lines[row + 1].is_heading

    def _grid_row(self, item: QListWidgetItem | None) -> int | None:
        if item is None:
            return None
        row = self.row(item)
        return self.lines[row].row if 0 <= row < len(self.lines) else None

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
        playing: str | None = None,
        activate_on_click: bool = False,
        on_dismiss: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(None)
        # A Tool window, which on Windows means no taskbar button: a browse is
        # something you open and dismiss, not a program that is running.  Left
        # a plain window it earns its own indicator, and — declaring no identity
        # of its own — Windows hangs that off whatever app it can pair it with.
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setWindowTitle(WINDOW_TITLE)
        self._handles = tuple(handles)
        self._on_pick = on_pick
        self._on_close = on_close
        self._path: tuple[str, ...] = ()

        self.grid = LibraryGrid(
            thumbnail_cache=thumbnail_cache, go_up=self.go_up, on_activate=self._activate,
        )
        if activate_on_click:
            self.grid.itemClicked.connect(lambda item: self._activate(self.grid.row(item)))
        self.index = FolderIndex(
            go_up=self.go_up, on_reveal=self.grid.reveal, on_activate=self._activate,
        )
        self.header = folder_header()
        self.header.linkActivated.connect(self._open_depth)
        self.dismiss_button = None
        if on_dismiss is not None:
            self.dismiss_button = dismiss_button(lambda: self._dismiss(on_dismiss))
            inside = QHBoxLayout(self.header)
            inside.setContentsMargins(0, 0, MARGIN_STANDARD, 0)
            inside.addStretch(1)
            inside.addWidget(self.dismiss_button)
        halves = QHBoxLayout()
        halves.setContentsMargins(0, 0, 0, 0)
        halves.setSpacing(0)
        halves.addWidget(self.index)
        halves.addWidget(self.grid, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addLayout(halves, 1)
        # Painted through the palette rather than a stylesheet: a QWidget
        # subclass draws neither its own background nor a stylesheet's unless it
        # paints one, and this is the sliver the two lists do not cover.
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, BG_PRIMARY)
        self.setPalette(palette)

        self.open_on(playing)
        # The grid takes the focus, though the sidebar is first in the layout and
        # would otherwise have it: the arrows and the type-ahead are the way the
        # browse is driven, and both belong on the tiles.
        self.grid.setFocus()

    def open_on(self, video: str | None) -> None:
        handle = handle_for(self._handles, video) if video else None
        if handle is None:
            self.open_folder(())
            return
        self.open_folder(folder_of(handle))
        if handle in self.grid.rows:
            row = self.grid.rows.index(handle)
            self.grid.reveal(row)
            self.index.reveal(row)

    def open_folder(self, path: Sequence[str]) -> None:
        """Show *path* in both halves: its folder tiles, or the videos it holds."""
        folder = folder_at(self._handles, path)
        self._path = folder.path
        self.header.setText(breadcrumbs(folder.path))
        self.grid.show_folder(folder)
        self.index.show_rows(self.grid.rows)

    def _dismiss(self, on_dismiss: Callable[[], None]) -> None:
        self.close()
        on_dismiss()

    def _open_depth(self, depth: str) -> None:
        self.open_folder(self._path[: int(depth)])

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
    groups: dict[str, list[IndexLine]] = {}
    for name, row in named:
        groups.setdefault(initial_letter(name), []).append(IndexLine(name, row))
    lines: list[IndexLine] = []
    every_letter = {NON_LETTER_HEADING, *string.ascii_uppercase, *groups}
    for letter in sorted(every_letter, key=lambda letter: (letter != NON_LETTER_HEADING, letter)):
        lines.append(IndexLine(letter))
        lines.extend(groups.get(letter, ()))
    return lines


def breadcrumbs(path: Sequence[str]) -> str:
    steps = [html.escape(step) for step in (TOP_LEVEL_NAME, *path)]
    link_style = f"color: {BLUE_LIGHT.name()}; text-decoration: none;"
    links = [
        f'<a href="{depth}" style="{link_style}">{step}</a>'
        for depth, step in enumerate(steps[:-1])
    ]
    return " / ".join([*links, steps[-1]])


def folder_header() -> QLabel:
    header = QLabel()
    header.setTextFormat(Qt.TextFormat.RichText)
    header.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
    header.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
    header.setStyleSheet(
        f"QLabel {{ background-color: {BG_SECONDARY.name()}; color: {TEXT_PRIMARY.name()};"
        " padding: 8px 12px; }"
    )
    return header


def dismiss_button(on_click: Callable[[], None]) -> QToolButton:
    button = QToolButton()
    button.setIcon(glyph_icon("cross", color=TEXT_PRIMARY, size=BUTTON_ICON))
    button.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setToolTip("Close")
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setStyleSheet(
        f"QToolButton {{ border: none; border-radius: {BUTTON_RADIUS}px;"
        f" background: {BG_BUTTON.name()}; }}"
        f" QToolButton:hover {{ background: {hovered(BG_BUTTON).name()}; }}"
    )
    button.clicked.connect(on_click)
    return button


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
    playing: str | None = None,
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
        NAMER.named_exe(python_exe, "LibraryBrowser"),
        "-m", "fun_time.library_browser", str(manifest_path), str(pick_file),
    ]
    if over is not None:
        x, y, width, height = over
        command += ["--x", str(x), "--y", str(y), "--width", str(width), "--height", str(height)]
    if playing:
        command += ["--playing", playing]
    runner(command, **hidden_subprocess_kwargs())

    try:
        return pick_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


@dataclass(frozen=True)
class BrowserConfig:
    """Where the browser reads the library, its families, and its stills from."""

    sources: str
    vr_sources: str
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
        sources=parser.get("media", "main_player_library_sources", fallback=""),
        vr_sources=parser.get("media", "vr_library_dirs", fallback=""),
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
    parser.add_argument("--playing", help="What the main player has up — the browse opens there")
    return parser.parse_args(argv)


def bring_the_browse_forward(window: QWidget) -> bool:
    # activateWindow is refused for this process; force_foreground_window says why.
    return force_foreground_window(int(window.winId()))


def main(argv: list[str] | None = None) -> int:
    from PyQt6.QtWidgets import QApplication

    args = parse_args(argv)
    # Claim Fun Time's identity before any window exists, so the browse is never
    # mistaken for an unrelated app's window (see the Tool flag above).
    from app_support.win32 import set_app_user_model_id

    from .win32_taskbar import APP_USER_MODEL_ID
    try:
        set_app_user_model_id(APP_USER_MODEL_ID)
    except OSError:
        pass  # Non-fatal — taskbar identity just falls back to the default

    app = QApplication.instance() or QApplication([])
    # The family's chrome, on the application: a tooltip is a top-level popup,
    # and only a sheet set here reaches it.
    app.setStyleSheet(family_stylesheet())

    config = load_browser_config(args.manifest_path)
    result_file = Path(args.result_file)
    window = LibraryBrowserWindow(
        handles_by_shape(config.sources, config.vr_sources, config.metadata_root),
        thumbnail_cache=config.thumbnail_cache,
        on_pick=lambda video: result_file.write_text(video, encoding="utf-8"),
        on_close=app.quit,
        playing=args.playing,
    )
    if None not in {args.x, args.y, args.width, args.height}:
        window.setGeometry(args.x, args.y, args.width, args.height)
    window.show()
    bring_the_browse_forward(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
