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
from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, cached_thumbnail, thumbnail_for

WINDOW_TITLE = "Fun Time Library"

# Tile size. Wide enough for a 16:9 still at the thumbnail cache's own longest
# edge, tall enough to carry two lines of title under it — library titles run
# long ("Jane Doe - Scene One"), and a name cut to one line is unrecognizable.
TILE_WIDTH = 200
TILE_HEIGHT = 168
ICON_WIDTH = 176
ICON_HEIGHT = 99

# A section header's band across the grid — tall enough to read, short enough
# that a section costs less than a row of tiles.
HEADER_HEIGHT = 34

# How often the grid picks up thumbnails the background extractor has finished.
THUMBNAIL_POLL_MS = 150


class LibraryBrowserWindow(QListWidget):
    """The grid of handles, one tile each, alphabetical as handed in.

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
    ) -> None:
        super().__init__(None)
        self._handles = tuple(handles)
        self._thumbnail_cache = Path(thumbnail_cache)
        self._on_pick = on_pick

        self.setWindowTitle(WINDOW_TITLE)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWordWrap(True)
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

        # One row per widget item, holding the handle it shows — or None where the
        # row is a section header.  Headers push every tile below them out of step
        # with the handle list, so the row is what a pick is read off.
        self.rows: list[LibraryHandle | None] = []
        section = None
        for handle in self._handles:
            if handle.section != section:
                section = handle.section
                self._add_row(self._header_item(section), None)
            self._add_row(self._tile_item(handle), handle)
        first_tile = next((row for row, handle in enumerate(self.rows) if handle), None)
        if first_tile is not None:
            self.setCurrentRow(first_tile)

        self.itemActivated.connect(self._pick)

        # Rows whose still is not cached yet are extracted off the event loop and
        # collected here; the timer below hands them to the grid.  A cold cache
        # would otherwise block the browse behind hundreds of HEVC decodes.
        self._extracted: queue.Queue[tuple[int, str]] = queue.Queue()
        self._extractor: threading.Thread | None = None
        self._collect_timer = QTimer(self)
        self._collect_timer.timeout.connect(self._collect_thumbnails)

    def _add_row(self, item: QListWidgetItem, handle: LibraryHandle | None) -> None:
        self.rows.append(handle)
        self.addItem(item)

    def _tile_item(self, handle: LibraryHandle) -> QListWidgetItem:
        item = QListWidgetItem(handle.title)
        item.setSizeHint(QSize(TILE_WIDTH, TILE_HEIGHT))
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        cached = cached_thumbnail(handle.preview, self._thumbnail_cache)
        if cached is not None:
            item.setIcon(QIcon(str(cached)))
        return item

    def _header_item(self, section: str) -> QListWidgetItem:
        """A section's name, spanning the row so its band starts on a fresh line.

        Not selectable and not enabled, so the arrow keys step from one band's
        last tile straight to the next's first — a header names a group of
        videos, it is not one you can play.
        """
        item = QListWidgetItem(section)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(QSize(self._header_width(), HEADER_HEIGHT))
        item.setFont(make_font(FONT_UI, SIZE_HEADING, bold=True))
        item.setForeground(TEXT_MUTED)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _header_width(self) -> int:
        """Wide enough that no tile fits beside a header, whatever the viewport."""
        return max(TILE_WIDTH, self.viewport().width() - 2 * self.spacing())

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Keep the headers spanning: a stale width lets tiles ride up beside one."""
        super().resizeEvent(event)
        width = self._header_width()
        for row, handle in enumerate(self.rows):
            if handle is None:
                self.item(row).setSizeHint(QSize(width, HEADER_HEIGHT))

    def start_thumbnail_extraction(self) -> None:
        """Fill in the stills the cache did not already have, in the background."""
        pending = rows_needing_stills(self.rows, self._thumbnail_cache)
        if not pending:
            return
        self._extractor = threading.Thread(
            target=self._extract, args=(pending,), daemon=True, name="library-thumbnails",
        )
        self._extractor.start()
        self._collect_timer.start(THUMBNAIL_POLL_MS)

    def _extract(self, rows: Sequence[int]) -> None:
        for row in rows:
            handle = self.rows[row]
            extracted = thumbnail_for(handle.preview, self._thumbnail_cache)
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

    def _pick(self, item: QListWidgetItem) -> None:
        handle = self.rows[self.row(item)]
        if handle is None:
            return
        self._on_pick(handle.video)
        self.close()


def rows_needing_stills(
    rows: Sequence[LibraryHandle | None], thumbnail_cache: str | Path
) -> list[int]:
    """Which rows still need a still extracted — the cache misses, in order.

    Header rows (``None``) picture nothing and are skipped.
    """
    return [
        row
        for row, handle in enumerate(rows)
        if handle is not None and cached_thumbnail(handle.preview, thumbnail_cache) is None
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
    app = QApplication.instance() or QApplication([])

    config = load_browser_config(args.manifest_path)
    result_file = Path(args.result_file)
    window = LibraryBrowserWindow(
        build_library_handles(config.sources, config.metadata_root),
        thumbnail_cache=config.thumbnail_cache,
        on_pick=lambda video: result_file.write_text(video, encoding="utf-8"),
    )
    if None not in {args.x, args.y, args.width, args.height}:
        window.setGeometry(args.x, args.y, args.width, args.height)
    window.show()
    window.activateWindow()
    window.raise_()
    window.start_thumbnail_extraction()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
