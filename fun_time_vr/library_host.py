from __future__ import annotations

import argparse
import hashlib
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app_support.frame_channel import FrameLooks, FrameWriter
from app_support.threading_utils import start_daemon_thread
from funestra.file_channel import append_command, consume_command_file
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget
from shared_ui.colors import dress_application

from fun_time.library_browser import (
    FLICKS_TOP_LEVEL_NAME,
    TOP_LEVEL_NAME,
    BrowseCallbacks,
    LibraryBrowserWindow,
    load_browser_config,
    load_flick_browser_config,
)
from fun_time.library_handles import LibraryHandle, handles_by_shape
from fun_time.modes import collect_video_files
from fun_time.win32_process import is_process_alive

from .library_listing_cache import remembered_listing
from .library_panel import (
    DISMISSED,
    HOVER,
    LIBRARY_SIZE_PX,
    OPEN,
    OPEN_FLICKS,
    SCROLL,
    picked_line,
)
from .offscreen_qt import as_rgba_bytes, offscreen_application
from .pointer import DRAG, PRESS, RELEASE

LISTING_FILENAME = "library_listing.json"

_POLL_MS = 15
_PARENT_CHECK_EVERY = 60


class HeadsetBrowse:
    def __init__(
        self, *, thumbnail_cache: Path, frames: FrameWriter, say: Callable[[str], None],
        flicks: Callable[[], Sequence[LibraryHandle]] = tuple,
    ) -> None:
        self._thumbnail_cache = thumbnail_cache
        self._say = say
        self._read_flicks = flicks
        self._library: LibraryBrowserWindow | None = None
        self._flicks: LibraryBrowserWindow | None = None
        self.window: LibraryBrowserWindow | None = None
        self._frames = frames
        self._token = 0
        self._open_when_it_lands: str | None = None
        self._pointer = QPoint()
        self._pressed: QWidget | None = None
        self._looks = FrameLooks()

    def take_the_library(self, handles: Sequence[LibraryHandle]) -> None:
        # A Qt widget belongs to the thread that made it, and the read is a worker's.
        self._library = self._browser(handles, TOP_LEVEL_NAME)
        if self.window is None:
            self.window = self._library
        if self._open_when_it_lands is not None:
            self._open(self._token, self._open_when_it_lands)
            self._open_when_it_lands = None

    def _browser(self, handles: Sequence[LibraryHandle], top_level_name: str) -> LibraryBrowserWindow:
        window = LibraryBrowserWindow(
            handles, thumbnail_cache=self._thumbnail_cache,
            callbacks=BrowseCallbacks(on_pick=lambda video: self._say(picked_line(video)),
                                      on_dismiss=lambda: self._say(DISMISSED)),
            activate_on_click=True,
            top_level_name=top_level_name,
        )
        window.resize(*LIBRARY_SIZE_PX)
        return window

    def _browse_flicks_afresh(self) -> LibraryBrowserWindow:
        if self._flicks is not None:
            self._flicks.deleteLater()
        self._flicks = self._browser(self._read_flicks(), FLICKS_TOP_LEVEL_NAME)
        return self._flicks

    def _put_away(self) -> None:
        if self.window is not None:
            self.window.close()
        self.window = None

    def apply(self, line: str) -> None:
        self._looks.ask()
        kind, _, rest = line.partition(" ")
        if kind == OPEN:
            token, _, video = rest.partition(" ")
            self._token = int(token)
            self._put_away()
            if self._library is None:
                self._open_when_it_lands = video
                return
            self.window = self._library
            self._open(int(token), video)
        elif kind == OPEN_FLICKS:
            token, _, flick = rest.partition(" ")
            self._open_when_it_lands = None
            self._put_away()
            self.window = self._browse_flicks_afresh()
            self._open(int(token), flick)
        elif self.window is None:
            return
        elif kind in (PRESS, DRAG, HOVER):
            x, y = (int(part) for part in rest.split())
            self._pointer = QPoint(x, y)
            self._point(kind)
        elif kind == RELEASE:
            self._mouse(self._pressed, QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)
            self._pressed = None
        elif kind == SCROLL:
            self._scroll(int(rest))

    def publish(self, now: float) -> None:
        if self.window is None or not self.window.isVisible():
            return
        if not self._looks.due(now):
            return
        width, height, pixels = as_rgba_bytes(self.window.grab().toImage())
        if self._looks.worth_sending(pixels):
            self._frames.write(self._token, width, height, pixels)

    def _open(self, token: int, video: str) -> None:
        self._token = token
        self._looks.forget_what_was_sent()
        self.window.show()
        self.window.open_on(video or None)

    def _point(self, kind: str) -> None:
        if kind == PRESS:
            self._pressed = self._under_the_pointer()
            self._mouse(self._pressed, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
        elif kind == DRAG:
            self._mouse(self._pressed, QEvent.Type.MouseMove, Qt.MouseButton.LeftButton)
        elif self._pressed is None:
            self._mouse(self._under_the_pointer(), QEvent.Type.MouseMove, Qt.MouseButton.NoButton)

    def _under_the_pointer(self) -> QWidget:
        return self.window.childAt(self._pointer) or self.window

    def _mouse(self, target: QWidget | None, kind: QEvent.Type, buttons: Qt.MouseButton) -> None:
        if target is None:
            return
        local = QPointF(target.mapFrom(self.window, self._pointer))
        QApplication.sendEvent(target, QMouseEvent(
            kind, local, QPointF(self._pointer), Qt.MouseButton.LeftButton, buttons,
            Qt.KeyboardModifier.NoModifier,
        ))

    def _scroll(self, delta: int) -> None:
        target = self._under_the_pointer()
        local = QPointF(target.mapFrom(self.window, self._pointer))
        QApplication.sendEvent(target, QWheelEvent(
            local, QPointF(self._pointer), QPoint(), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        ))


def serve_once(
    browse: HeadsetBrowse, asked: Path, *, parent_alive: Callable[[], bool], now: float,
) -> bool:
    if not parent_alive():
        return False
    for line in consume_command_file(asked, uppercase=False):
        browse.apply(line)
    browse.publish(now)
    return True


def read_the_library(config, kept: Path, *, afresh: bool = False) -> list[LibraryHandle]:
    try:
        return handles_by_shape(
            config.sources, config.vr_sources, config.metadata_root,
            listing=lambda sources: remembered_listing(
                kept.with_name(f"{kept.stem}_{_listing_key(sources)}{kept.suffix}"),
                lambda: collect_video_files(sources), afresh=afresh),
        )
    except OSError:
        return []


def read_the_flicks(config) -> list[LibraryHandle]:
    try:
        return config.handles()
    except OSError:
        return []


def _read_a_syncing_drive_off_the_serving_thread(config, kept: Path, landed: list) -> None:
    start_daemon_thread(
        target=lambda: landed.append(read_the_library(config, kept)), name="library-read")
    start_daemon_thread(
        target=lambda: read_the_library(config, kept, afresh=True), name="library-reread")


def _listing_key(sources: str) -> str:
    return hashlib.sha256(sources.encode("utf-8")).hexdigest()[:12]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The library browser, run for the headset")
    parser.add_argument("manifest_path", type=Path)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--parent", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with offscreen_application() as app:
        dress_application(app)
        config = load_browser_config(args.manifest_path)
        flicks = load_flick_browser_config(args.manifest_path)
        width, height = LIBRARY_SIZE_PX
        frames = FrameWriter(args.frames, max_pixels=width * height)
        browse = HeadsetBrowse(
            thumbnail_cache=config.thumbnail_cache, frames=frames,
            say=lambda line: append_command(args.output, line),
            flicks=lambda: read_the_flicks(flicks),
        )
        kept = args.input.with_name(LISTING_FILENAME)
        landed: list[Sequence[LibraryHandle]] = []
        _read_a_syncing_drive_off_the_serving_thread(config, kept, landed)
        ticks = 0

        def parent_alive() -> bool:
            nonlocal ticks
            ticks += 1
            return ticks % _PARENT_CHECK_EVERY != 0 or is_process_alive(args.parent)

        def tick() -> None:
            if browse.window is None and landed:
                browse.take_the_library(landed.pop())
            if not serve_once(browse, args.input, parent_alive=parent_alive, now=time.monotonic()):
                app.quit()

        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(_POLL_MS)
        try:
            return app.exec()
        finally:
            frames.close()


if __name__ == "__main__":
    raise SystemExit(main())
