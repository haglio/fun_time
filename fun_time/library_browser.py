"""Fun Time's own browser for the primary library.

The Windows file dialog this replaces browsed the library the way it sits on
disk — stage folders nested several deep, the same video filed under three of
them — so finding a video meant remembering how far it had got through the
pipeline.  This browses :mod:`fun_time.library_handles` instead: one tile per
video, whatever renditions it exists as, alphabetical, with a still off each.

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
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QListWidget, QListWidgetItem

from shared_ui.colors import BG_PRIMARY, BG_SECONDARY, BLUE, TEXT_MUTED, TEXT_PRIMARY
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_HEADING, make_font

from .library_handles import LibraryHandle, build_library_handles
from .library_tree import SubFolder, folder_at
from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, cached_thumbnail, thumbnail_for

WINDOW_TITLE = "Fun Time Library"

# Tile size. Wide enough for a 16:9 still at the thumbnail cache's own longest
# edge, tall enough to carry two lines of title under it — library titles run
# long ("Jane Doe - Scene One"), and a name cut to one line is unrecognizable.
TILE_WIDTH = 200
TILE_HEIGHT = 168
ICON_WIDTH = 176
ICON_HEIGHT = 99

# What the tile that goes back up is called, at the two places it can appear.
UP_LABEL = "back"
UP_TO_LIBRARY = "all folders"

# How often the grid picks up thumbnails the background extractor has finished.
THUMBNAIL_POLL_MS = 150


class LibraryBrowserWindow(QListWidget):
    """A grid you walk: folder tiles you open, then the videos inside them.

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
        self._thumbnail_cache = Path(thumbnail_cache)
        self._on_pick = on_pick
        self._on_close = on_close
        self._path: tuple[str, ...] = ()

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(ICON_WIDTH, ICON_HEIGHT))
        self.setSpacing(6)
        self.setFont(make_font(FONT_UI, SIZE_BODY))
        self.setStyleSheet(
            f"QListWidget {{ background-color: {BG_PRIMARY.name()};"
            f" color: {TEXT_PRIMARY.name()}; border: none; }}"
            f" QListWidget::item {{ background-color: {BG_SECONDARY.name()};"
            " border-radius: 4px; padding: 4px; }"
            f" QListWidget::item:selected {{ background-color: {BLUE.name()}; }}"
        )

        # One entry per widget row: the handle a video tile plays, the SubFolder
        # a folder tile opens, or None for the tile that goes back up.  Rows and
        # handles do not line up once the grid is walkable, so what a row *is* is
        # read from here rather than counted.
        self.rows: list[LibraryHandle | SubFolder | None] = []
        # One signal covers both gestures: Qt emits itemActivated for Enter
        # AND at the end of a double-click, so nothing here needs to know
        # which of the two the user made.
        self.itemActivated.connect(self._activate)

        # Rows whose still is not cached yet are extracted off the event loop and
        # collected here; the timer below hands them to the grid.  A cold cache
        # would otherwise block the browse behind hundreds of HEVC decodes.
        self._extracted: queue.Queue[tuple[int, str]] = queue.Queue()
        self._extractor: threading.Thread | None = None
        self._collect_timer = QTimer(self)
        self._collect_timer.timeout.connect(self._collect_thumbnails)

        self.open_folder(())

    def open_folder(self, path: Sequence[str]) -> None:
        """Show *path*: its folder tiles, or the videos it holds."""
        folder = folder_at(self._handles, path)
        self._path = folder.path
        self.clear()
        self.rows = []
        self.setWindowTitle(f"{WINDOW_TITLE} — {folder.title}" if folder.title else WINDOW_TITLE)
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

    def _add_row(self, item: QListWidgetItem, what: LibraryHandle | SubFolder | None) -> None:
        self.rows.append(what)
        self.addItem(item)

    def _tile_item(self, handle: LibraryHandle) -> QListWidgetItem:
        return self._pictured_item(handle.title, handle.preview)

    def _folder_item(self, child: SubFolder) -> QListWidgetItem:
        """A folder tile: its name, how much is in it, and a still from inside."""
        return self._pictured_item(f"{child.name}  ({child.count})", child.preview)

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
            item.setIcon(QIcon(str(cached)))
        return item

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
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

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Backspace goes back up, the way it does in every other file browser."""
        if event.key() == Qt.Key.Key_Backspace:
            self.go_up()
            return
        super().keyPressEvent(event)

    def go_up(self) -> None:
        """Leave the folder being shown for the one that holds it."""
        if self._path:
            self.open_folder(self._path[:-1])

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
            extracted = thumbnail_for(showing[row].preview, self._thumbnail_cache)
            if extracted is not None:
                self._extracted.put((row, str(extracted)))

    def _collect_thumbnails(self) -> None:
        while True:
            try:
                row, path = self._extracted.get_nowait()
            except queue.Empty:
                break
            item = self.item(row)
            if item is not None:
                item.setIcon(QIcon(path))
        if self._extractor is not None and not self._extractor.is_alive():
            self._collect_timer.stop()

    def _activate(self, item: QListWidgetItem) -> None:
        """Open a folder, go back up, or play a video — whatever the row is."""
        what = self.rows[self.row(item)]
        if what is None:
            self.go_up()
        elif isinstance(what, SubFolder):
            self.open_folder((*self._path, what.name))
        else:
            self._on_pick(what.video)
            self.close()


def rows_needing_stills(rows: Sequence[object], thumbnail_cache: str | Path) -> list[int]:
    """Which rows still need a still extracted — the cache misses, in order.

    The go-back row (``None``) pictures nothing and is skipped.
    """
    return [
        row
        for row, what in enumerate(rows)
        if what is not None and cached_thumbnail(what.preview, thumbnail_cache) is None
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

    command = [python_exe, "-m", "fun_time.library_browser", str(manifest_path), str(pick_file)]
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
    disagree with the session about which folders are the primary library.
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
    parser = argparse.ArgumentParser(description="Browse the Fun Time primary library")
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
    from .win32 import APP_USER_MODEL_ID, set_app_user_model_id
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
