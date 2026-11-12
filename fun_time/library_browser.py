from __future__ import annotations

import argparse
import configparser
import contextlib
import queue
import string
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.win32 import set_app_user_model_id
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shared_ui.application import application
from shared_ui.chrome import picked_item_rules
from shared_ui.colors import (
    BG_BUTTON,
    BG_PRIMARY,
    BG_SECONDARY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    dress_application,
    hovered,
)
from shared_ui.folder_path import FolderPath
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_HEADING, make_font
from shared_ui.foreground import bring_to_front
from shared_ui.icon_button import fill_square_with_icon
from shared_ui.icons import qt_icon
from shared_ui.spacing import (
    BUTTON_ICON,
    BUTTON_RADIUS,
    GAP_MEDIUM,
    GAP_SMALL,
    HAIRLINE,
    MARGIN_DIALOG,
    MARGIN_STANDARD,
)

from . import preview_marker
from .browse_records import GENAU_FLAG, BrowseCallbacks, BrowseOpening
from .checkout_overrides import sibling_checkouts_kwargs
from .folder_listings import FolderListings
from .library_handles import (
    LibraryHandle,
    genau_flick_sources,
    genau_vr_flicks,
    handle_for,
    handles_by_shape,
)
from .library_tree import Folder, SubFolder, folder_at, folder_of
from .process_identity import NAMER
from .thumbnail_cache import cached_thumbnail, hud_and_browser_thumbnail_cache, thumbnail_for

WINDOW_TITLE = "Fun Time Library"
TOP_LEVEL_NAME = "Library"
FLICKS_TOP_LEVEL_NAME = "Genau"

ICON_WIDTH = 176
ICON_HEIGHT = ICON_WIDTH * 9 // 16
ROOM_FOR_TWO_LINES_OF_TITLE = 69
TILE_WIDTH = 200
TILE_HEIGHT = ICON_HEIGHT + ROOM_FOR_TWO_LINES_OF_TITLE

SIDEBAR_WIDTH = TILE_WIDTH * 11 // 10

NON_LETTER_HEADING = "#"
SKIPPED_BY_ARROWS_AND_TYPE_AHEAD = Qt.ItemFlag.NoItemFlags

WAY_BACK_UP = None
UP_LABEL = "back"
UP_TO_LIBRARY = "all folders"

MONTAGE_GAP = HAIRLINE

THUMBNAIL_POLL_MS = 150


class BrowseList(QListWidget):
    def __init__(self, go_up: Callable[[], None]) -> None:
        super().__init__(None)
        self._go_up = go_up
        self.setFont(make_font(FONT_UI, SIZE_BODY))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Backspace:
            self._go_up()
            return
        super().keyPressEvent(event)

    def land_mid_view_on(self, row: int) -> None:
        self.setCurrentRow(row)
        self.scrollToItem(self.item(row), QAbstractItemView.ScrollHint.PositionAtCenter)


class LibraryGrid(BrowseList):
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
        self.setSpacing(GAP_MEDIUM)
        self.setStyleSheet(
            f"QListWidget {{ background-color: {BG_PRIMARY.name()};"
            f" color: {TEXT_PRIMARY.name()}; border: none; }}"
            f" QListWidget::item {{ background-color: {BG_SECONDARY.name()};"
            f" border-radius: {BUTTON_RADIUS}px; padding: {GAP_SMALL}px; }}"
            + picked_item_rules("QListWidget")
        )

        self.rows: list[LibraryHandle | SubFolder | None] = []
        # One signal covers both gestures: Qt emits itemActivated for Enter
        # AND at the end of a double-click, so nothing here needs to know
        # which of the two the user made.
        self.itemActivated.connect(lambda item: on_activate(self.row(item)))

        self._extraction: _StillDecodesOffTheEventLoop | None = None
        self._collect_timer = QTimer(self)
        self._collect_timer.timeout.connect(self._collect_thumbnails)

    def show_folder(self, folder: Folder) -> None:
        self.clear()
        self.rows = []
        listings = FolderListings()
        if folder.parent is not None:
            self._add_row(self._up_item(folder.parent), WAY_BACK_UP)
        for child in folder.children:
            self._add_row(self._folder_item(child, listings), child)
        for handle in folder.handles:
            self._add_row(self._tile_item(handle, listings), handle)
        self.setCurrentRow(min(1 if folder.parent is not None else 0, self.count() - 1))
        self.start_thumbnail_extraction(listings)

    def reveal(self, row: int) -> None:
        if self.item(row) is not None:
            self.land_mid_view_on(row)

    def _add_row(self, item: QListWidgetItem, what: LibraryHandle | SubFolder | None) -> None:
        self.rows.append(what)
        self.addItem(item)

    def _tile_item(self, handle: LibraryHandle, listings: FolderListings) -> QListWidgetItem:
        return self._thumbnailed_item(handle.title, handle.preview, listings)

    def _folder_item(self, child: SubFolder, listings: FolderListings) -> QListWidgetItem:
        item = QListWidgetItem(f"{child.name}  ({child.count})")
        item.setSizeHint(QSize(TILE_WIDTH, TILE_HEIGHT))
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        thumbnails = self._cached_stills(child, listings)
        if thumbnails:
            item.setIcon(montage_icon(thumbnails))
        return item

    def _cached_stills(
        self, what: LibraryHandle | SubFolder, listings: FolderListings
    ) -> list[Path]:
        return [
            cached for cached in (
                cached_thumbnail(preview, self._thumbnail_cache, listings)
                for preview in previews_of(what)
            ) if cached is not None
        ]

    def _up_item(self, parent: tuple[str, ...]) -> QListWidgetItem:
        item = QListWidgetItem(UP_LABEL if parent else UP_TO_LIBRARY)
        item.setSizeHint(QSize(TILE_WIDTH, TILE_HEIGHT))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
        item.setForeground(TEXT_MUTED)
        return item

    def _thumbnailed_item(
        self, label: str, preview: str, listings: FolderListings
    ) -> QListWidgetItem:
        item = QListWidgetItem(label)
        item.setSizeHint(QSize(TILE_WIDTH, TILE_HEIGHT))
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        cached = cached_thumbnail(preview, self._thumbnail_cache, listings)
        if cached is not None:
            item.setIcon(fitted_icon(cached))
        return item

    def start_thumbnail_extraction(self, listings: FolderListings | None = None) -> None:
        self.stop_thumbnail_extraction()
        pending = rows_needing_stills(self.rows, self._thumbnail_cache, listings)
        if pending:
            self._extraction = _StillDecodesOffTheEventLoop(
                [(row, previews_of(self.rows[row])) for row in pending], self._thumbnail_cache,
            )
            self._extraction.start()
            self._collect_timer.start(THUMBNAIL_POLL_MS)

    def stop_thumbnail_extraction(self) -> None:
        if self._extraction is not None:
            self._extraction.stop()
        self._extraction = None
        self._collect_timer.stop()

    def _collect_thumbnails(self) -> None:
        extraction = self._extraction
        finished = not extraction.is_alive()
        listings = FolderListings()
        while not extraction.extracted.empty():
            row = extraction.extracted.get_nowait()
            item, what = self.item(row), self.rows[row]
            thumbnails = self._cached_stills(what, listings)
            if thumbnails:
                item.setIcon(montage_icon(thumbnails) if isinstance(what, SubFolder)
                             else fitted_icon(thumbnails[0]))
        if finished:
            self._collect_timer.stop()


class _StillDecodesOffTheEventLoop(threading.Thread):
    def __init__(self, pending: Sequence[tuple[int, Sequence[str]]], thumbnail_cache: Path) -> None:
        super().__init__(daemon=True, name="library-thumbnails")
        self._pending = pending
        self._thumbnail_cache = thumbnail_cache
        self._stopped = threading.Event()
        self.extracted: queue.Queue[int] = queue.Queue()

    def stop(self) -> None:
        self._stopped.set()

    def run(self) -> None:
        listings = FolderListings()
        for row, previews in self._pending:
            for preview in previews:
                if self._stopped.is_set():
                    return
                thumbnail_for(preview, self._thumbnail_cache, listings=listings)
            self.extracted.put(row)


class _MarkAfterTheLetterInItsInk(QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.decorationPosition = QStyleOptionViewItem.Position.Right
        never_the_gray_qt_gives_a_flagless_row = QStyle.StateFlag.State_Enabled
        option.state |= never_the_gray_qt_gives_a_flagless_row


class FolderIndex(BrowseList):
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
        self.setItemDelegate(_MarkAfterTheLetterInItsInk(self))
        self.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
        self.setStyleSheet(
            f"QListWidget {{ background-color: {BG_SECONDARY.name()};"
            f" color: {TEXT_PRIMARY.name()};"
            f" border: none; border-right: 1px solid {BG_PRIMARY.name()}; }}"
            f" QListWidget::item {{ padding: {GAP_SMALL}px {MARGIN_STANDARD}px; }}"
            + picked_item_rules("QListWidget")
        )

        self.lines: list[IndexLine] = []
        self._open_heading: int | None = None
        self.currentItemChanged.connect(self._reveal)
        self.itemActivated.connect(self._activate)

    def show_rows(self, rows: Sequence[object]) -> None:
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
        # A Qt list view sizes its rows to the widest size hint, and stretches one narrower than the
        # viewport, so names elide at the sidebar's edge.
        for row in range(self.count()):
            self.item(row).setSizeHint(QSize(1, self.sizeHintForRow(row)))

    def _heading_item(self, letter: str) -> QListWidgetItem:
        item = QListWidgetItem(letter)
        item.setFlags(SKIPPED_BY_ARROWS_AND_TYPE_AHEAD)
        item.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
        return item

    def _name_item(self, name: str) -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setToolTip(name)
        return item

    def reveal(self, grid_row: int) -> None:
        row = next((r for r, line in enumerate(self.lines) if line.row == grid_row), None)
        if row is None:
            return
        self._open_only(self._heading_above(row))
        self.land_mid_view_on(row)

    def _heading_above(self, row: int) -> int:
        return next(r for r in range(row, -1, -1) if self.lines[r].is_heading)

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        row = self.row(item) if item is not None else -1
        qt_signals_no_click_here = 0 <= row < len(self.lines) and self.lines[row].is_heading
        if qt_signals_no_click_here:
            self._press_heading(row)
        else:
            super().mousePressEvent(event)

    def _press_heading(self, row: int) -> None:
        if not self._has_names(row):
            return
        if row == self._open_heading:
            self._open_only(None)
            return
        self._open_only(row)
        self._choose(row + 1)

    def _choose(self, row: int) -> None:
        qt_will_report_no_change = row == self.currentRow()
        self.setCurrentRow(row)
        if qt_will_report_no_change:
            self._reveal(self.item(row))

    def _open_only(self, heading_row: int | None) -> None:
        self._open_heading = heading_row
        heading = None
        marks = {"open": qt_icon("chevron_down"), "closed": qt_icon("chevron_right"),
                 "empty": QIcon()}
        for row, line in enumerate(self.lines):
            if line.is_heading:
                heading = row
                self.item(row).setIcon(marks[self._letter_state(row)])
                self.item(row).setForeground(TEXT_PRIMARY if self._has_names(row) else TEXT_MUTED)
            else:
                self.setRowHidden(row, heading != heading_row)

    def _letter_state(self, row: int) -> str:
        if row == self._open_heading:
            return "open"
        return "closed" if self._has_names(row) else "empty"

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
    def __init__(
        self,
        handles: Sequence[LibraryHandle],
        *,
        thumbnail_cache: str | Path,
        callbacks: BrowseCallbacks,
        activate_on_click: bool = False,
        top_level_name: str = TOP_LEVEL_NAME,
    ) -> None:
        super().__init__(None)
        # A Tool window, which on Windows means no taskbar button: a browse is
        # something you open and dismiss, not a program that is running.  Left
        # a plain window it earns its own indicator, and — declaring no identity
        # of its own — Windows hangs that off whatever app it can pair it with.
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setWindowTitle(WINDOW_TITLE)
        self._handles = tuple(handles)
        self._on_pick = callbacks.on_pick
        self._on_close = callbacks.on_close
        self._top_level_name = top_level_name
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
        self.header.step_clicked.connect(self._open_depth)
        self.dismiss_button = None
        if callbacks.on_dismiss is not None:
            self.dismiss_button = dismiss_button(lambda: self._dismiss(callbacks.on_dismiss))
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

        self.open_folder(())
        self.grid.setFocus()

    def open_on(self, video: str | None) -> None:
        handle = handle_for(self._handles, video) if video else None
        if handle is None:
            self.open_folder(())
            return
        beside_what_is_up = folder_of(handle)
        self.open_folder(beside_what_is_up)
        if handle in self.grid.rows:
            row = self.grid.rows.index(handle)
            self.grid.reveal(row)
            self.index.reveal(row)

    def open_folder(self, path: Sequence[str]) -> None:
        folder = folder_at(self._handles, path)
        self._path = folder.path
        self.header.set_path([self._top_level_name, *folder.path])
        self.grid.show_folder(folder)
        self.index.show_rows(self.grid.rows)

    def _dismiss(self, on_dismiss: Callable[[], None]) -> None:
        self.close()
        on_dismiss()

    def _open_depth(self, depth: int) -> None:
        self.open_folder(self._path[:depth])

    def go_up(self) -> None:
        if self._path:
            self.open_folder(self._path[:-1])

    def closeEvent(self, event) -> None:
        # Qt does not count a Tool window when it quits on the last window closed, so closing this
        # cannot end the event loop.
        super().closeEvent(event)
        self.grid.stop_thumbnail_extraction()
        if self._on_close is not None:
            self._on_close()

    def _activate(self, row: int) -> None:
        what = self.grid.rows[row]
        if what is WAY_BACK_UP:
            self.go_up()
        elif isinstance(what, SubFolder):
            self.open_folder((*self._path, what.name))
        else:
            self._on_pick(what.video)
            self.close()


@dataclass(frozen=True)
class IndexLine:
    label: str
    row: int | None = None

    @property
    def is_heading(self) -> bool:
        return self.row is None


def name_of(what: LibraryHandle | SubFolder | None) -> str:
    return what.display_name if what is not WAY_BACK_UP else ""


def initial_letter(name: str) -> str:
    first = name.strip()[:1].upper()
    return first if first.isalpha() else NON_LETTER_HEADING


def alphabetical_index(rows: Sequence[object]) -> list[IndexLine]:
    named = sorted(
        ((name_of(what), row) for row, what in enumerate(rows) if what is not WAY_BACK_UP),
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


def folder_header() -> FolderPath:
    header = FolderPath(" / ")
    header.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
    header.setStyleSheet(
        f"QLabel {{ background-color: {BG_SECONDARY.name()}; color: {TEXT_PRIMARY.name()};"
        f" padding: {MARGIN_STANDARD}px {MARGIN_DIALOG}px; }}"
    )
    return header


def dismiss_button(on_click: Callable[[], None]) -> QToolButton:
    button = fill_square_with_icon(QToolButton())
    button.setIcon(qt_icon("cross", color=TEXT_PRIMARY))
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
    # Qt would draw the icon at exactly the icon size, stretching a thumbnail that is not the tile's
    # shape.
    return QIcon(_fitted(still, ICON_WIDTH, ICON_HEIGHT))


def _fitted(still: str | Path, width: int, height: int) -> QPixmap:
    return QPixmap(str(still)).scaled(
        width, height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def montage_icon(thumbnails: Sequence[str | Path]) -> QIcon:
    if len(thumbnails) == 1:
        return fitted_icon(thumbnails[0])
    cell_width = (ICON_WIDTH - MONTAGE_GAP) // 2
    cell_height = (ICON_HEIGHT - MONTAGE_GAP) // 2
    canvas = QPixmap(ICON_WIDTH, ICON_HEIGHT)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        for index, still in enumerate(thumbnails[:4]):
            thumbnail = _fitted(still, cell_width, cell_height)
            left = (index % 2) * (cell_width + MONTAGE_GAP)
            top = (index // 2) * (cell_height + MONTAGE_GAP)
            painter.drawPixmap(
                left + (cell_width - thumbnail.width()) // 2,
                top + (cell_height - thumbnail.height()) // 2,
                thumbnail,
            )
    finally:
        painter.end()
    return QIcon(canvas)


def previews_of(what: LibraryHandle | SubFolder | None) -> tuple[str, ...]:
    return what.previews if what is not WAY_BACK_UP else ()


def rows_needing_stills(
    rows: Sequence[object], thumbnail_cache: str | Path,
    listings: FolderListings | None = None,
) -> list[int]:
    dated = listings or FolderListings()
    return [
        row
        for row, what in enumerate(rows)
        if any(
            cached_thumbnail(preview, thumbnail_cache, dated) is None
            for preview in previews_of(what)
        )
    ]


PICK_FILENAME = "library_browser_pick.txt"


def pick_file_for(manifest_path: str | Path) -> Path:
    return Path(manifest_path).parent / PICK_FILENAME


def browse_library(
    manifest_path: str | Path,
    python_exe: str,
    opening: BrowseOpening,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str | None:
    pick_file = pick_file_for(manifest_path)
    pick_file.unlink(missing_ok=True)

    command = [
        NAMER.named_exe(python_exe, "LibraryBrowser"),
        "-m", "fun_time.library_browser", str(manifest_path), str(pick_file),
    ]
    command += opening.arguments()
    sibling_checkouts = _read_manifest(manifest_path).get(
        "runtime", "sibling_checkouts", fallback="")
    runner(command, **hidden_subprocess_kwargs(), **sibling_checkouts_kwargs(sibling_checkouts))

    try:
        return pick_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


@dataclass(frozen=True)
class BrowserConfig:
    sources: str
    vr_sources: str
    metadata_root: Path | None
    thumbnail_cache: Path
    top_level_name: str = TOP_LEVEL_NAME

    def handles(self) -> list[LibraryHandle]:
        return handles_by_shape(self.sources, self.vr_sources, self.metadata_root)


def _read_manifest(manifest_path: str | Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(manifest_path, encoding="utf-8")
    return parser


def load_browser_config(manifest_path: str | Path) -> BrowserConfig:
    parser = _read_manifest(manifest_path)
    metadata_root = parser.get("regen", "metadata_root", fallback="")
    return BrowserConfig(
        sources=parser.get("media", "main_funestra_library_sources", fallback=""),
        vr_sources=parser.get("media", "vr_library_dirs", fallback=""),
        metadata_root=Path(metadata_root) if metadata_root else None,
        thumbnail_cache=hud_and_browser_thumbnail_cache(Path(manifest_path).parent),
    )


def load_flick_browser_config(manifest_path: str | Path) -> BrowserConfig:
    flicks_folder = _read_manifest(manifest_path).get("media", "genau_flicks", fallback="")
    return BrowserConfig(
        sources=genau_flick_sources(flicks_folder),
        vr_sources=genau_vr_flicks(flicks_folder),
        metadata_root=None,
        thumbnail_cache=hud_and_browser_thumbnail_cache(Path(manifest_path).parent),
        top_level_name=FLICKS_TOP_LEVEL_NAME,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse the Fun Time main library")
    parser.add_argument("manifest_path", help="Path to the Windows bridge launch manifest")
    parser.add_argument("result_file", help="Where to write the chosen video path")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--playing", help="What the Main Funestra has up — the browse opens there")
    parser.add_argument(GENAU_FLAG, action="store_true", help="Browse Genau instead")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with contextlib.suppress(OSError):
        set_app_user_model_id(preview_marker.session_identity(preview_marker.shown_as()))

    with application([]) as app:
        dress_application(app)

        config = (load_flick_browser_config if args.genau else load_browser_config)(args.manifest_path)
        result_file = Path(args.result_file)
        window = LibraryBrowserWindow(
            config.handles(),
            thumbnail_cache=config.thumbnail_cache,
            callbacks=BrowseCallbacks(
                on_pick=lambda video: result_file.write_text(video, encoding="utf-8"),
                on_close=app.quit,
            ),
            top_level_name=config.top_level_name,
        )
        window.open_on(args.playing)
        if None not in {args.x, args.y, args.width, args.height}:
            window.setGeometry(args.x, args.y, args.width, args.height)
        window.show()
        bring_to_front(window)
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
