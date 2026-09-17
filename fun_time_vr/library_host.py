from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from player_core.file_channel import append_command, consume_command_file
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import QImage, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget
from shared_ui.chrome import family_stylesheet

from fun_time.library_browser import LibraryBrowserWindow, load_browser_config
from fun_time.library_handles import LibraryHandle, handles_by_shape
from fun_time.win32_process import is_process_alive

from .frame_channel import FrameWriter
from .library_panel import (
    DISMISSED,
    HOVER,
    LIBRARY_SIZE_PX,
    OPEN,
    SCROLL,
    picked_line,
)
from .pointer import DRAG, PRESS, RELEASE

_POLL_MS = 15
_PARENT_CHECK_EVERY = 60
_IDLE_LOOK_S = 0.1


class HeadsetBrowse:
    def __init__(
        self, handles: Sequence[LibraryHandle], *, thumbnail_cache: Path,
        frames: FrameWriter, say: Callable[[str], None],
    ) -> None:
        self.window = LibraryBrowserWindow(
            handles, thumbnail_cache=thumbnail_cache,
            on_pick=lambda video: say(picked_line(video)),
            on_dismiss=lambda: say(DISMISSED),
            activate_on_click=True,
        )
        self.window.resize(*LIBRARY_SIZE_PX)
        self._frames = frames
        self._token = 0
        self._sent: bytes | None = None
        self._pointer = QPoint()
        self._pressed: QWidget | None = None
        self._asked = False
        self._looked_at: float | None = None

    def apply(self, line: str) -> None:
        self._asked = True
        kind, _, rest = line.partition(" ")
        if kind == OPEN:
            token, _, video = rest.partition(" ")
            self._open(int(token), video)
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
        if not self.window.isVisible():
            return
        idle = not self._asked
        if idle and self._looked_at is not None and now - self._looked_at < _IDLE_LOOK_S:
            return
        self._asked = False
        self._looked_at = now
        image = self.window.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = image.constBits().asstring(image.sizeInBytes())
        if pixels == self._sent:
            return
        self._frames.write(self._token, image.width(), image.height(), pixels)
        self._sent = pixels

    def _open(self, token: int, video: str) -> None:
        self._token = token
        self._sent = None
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


def _windows_fonts_offscreen_qt_lacks() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


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
    os.environ.setdefault("QT_QPA_FONTDIR", str(_windows_fonts_offscreen_qt_lacks()))
    app = QApplication([sys.argv[0], "-platform", "offscreen"])
    app.setStyleSheet(family_stylesheet())
    config = load_browser_config(args.manifest_path)
    try:
        handles = handles_by_shape(config.sources, config.vr_sources, config.metadata_root)
    except OSError:
        handles = []
    width, height = LIBRARY_SIZE_PX
    frames = FrameWriter(args.frames, max_pixels=width * height)
    browse = HeadsetBrowse(
        handles, thumbnail_cache=config.thumbnail_cache, frames=frames,
        say=lambda line: append_command(args.output, line),
    )
    ticks = 0

    def parent_alive() -> bool:
        nonlocal ticks
        ticks += 1
        return ticks % _PARENT_CHECK_EVERY != 0 or is_process_alive(args.parent)

    def tick() -> None:
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
