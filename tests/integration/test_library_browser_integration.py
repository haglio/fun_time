"""Real-window checks for the main player library browser.

The browser's whole value is what the user *reads* off it — the handle names
under the stills — and the unit suite cannot see that: it renders on Qt's
offscreen platform, where ``QFontDatabase.families()`` is empty and every glyph
comes out as a missing-character box.  A grid of tofu passes every assertion a
unit test can make about it.  So the "does it actually paint the titles" check
lives here, on the native platform, where the fonts are real.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PyQt6.QtCore import QEvent, QPointF, Qt, QTimer
from PyQt6.QtGui import QFontDatabase, QMouseEvent
from PyQt6.QtWidgets import QApplication

from fun_time.library_browser import LibraryBrowserWindow
from fun_time.library_handles import CLIPS_SUFFIX, LibraryHandle
from fun_time.thumbnail_cache import thumbnail_path

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="paints a real Qt window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

TITLES = ("Alpha Studio - Scene One", "Beta Collective - The Long Afternoon 2")
SECTIONS = ("big_batch", "big_batch" + CLIPS_SUFFIX)


def _handles(tmp_path: Path, cache: Path) -> list[LibraryHandle]:
    """One handle per section, each with a still already cached, so nothing is
    extracted — and so the grid has a header of each kind to paint."""
    handles = []
    for index, title in enumerate(TITLES):
        video = tmp_path / f"v{index}.mp4"
        video.write_bytes(b"\0" * (100 + index))
        picture = Image.new("RGB", (176, 99), (40, 60, 90))
        ImageDraw.Draw(picture).rectangle((10, 10, 60, 60), fill=(200, 120, 40))
        picture.save(thumbnail_path(video, cache), "JPEG")
        handles.append(
            LibraryHandle(title=title, versions=(str(video),), section=SECTIONS[index])
        )
    return handles


def test_the_grid_paints_its_titles_and_stills(tmp_path: Path):
    """A rendered tile carries both the still and legible text, not tofu.

    Legibility is read off the font: on the native platform the families are
    there and the title's glyphs are really in the face being drawn with, where
    offscreen has no font at all and paints every character as a box.  Section
    headers are held to the same bar — the separator in one is a character too,
    and a header nobody can read names no section.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    handles = _handles(tmp_path, cache)

    window = LibraryBrowserWindow(handles, thumbnail_cache=cache, on_pick=lambda _v: None)
    try:
        window.setGeometry(0, 0, 600, 300)
        window.show()
        painted = window.grab().toImage()

        assert QFontDatabase.families(), "native platform must have real fonts"
        metrics = window.grid.fontMetrics()
        assert metrics.horizontalAdvance(TITLES[0]) > 0
        for character in set("".join(TITLES + SECTIONS)):
            assert metrics.inFont(character), f"{character!r} would paint as a box"

        tiles = [row for row, handle in enumerate(window.grid.rows) if handle is not None]
        assert not window.grid.item(tiles[0]).icon().isNull()
        assert painted.width() > 0 and painted.height() > 0
        colors = {painted.pixel(x, y) for x in range(0, painted.width(), 7)
                  for y in range(0, painted.height(), 7)}
        assert len(colors) > 3, "the grid painted a flat slab, not tiles"
    finally:
        window.close()


def test_the_browser_window_owns_no_taskbar_button(tmp_path: Path):
    """The mirror of the dashboard's check, and the opposite answer.

    The dashboard is a program you leave running, so it carries WS_EX_APPWINDOW
    and shows on the taskbar.  A browse is a window you open and dismiss, so it
    carries WS_EX_TOOLWINDOW instead and shows nowhere — which is what stops it
    turning up as an "open" mark against some unrelated app's icon.  Only the
    real Qt windows platform gives winId() a genuine top-level HWND to read.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    window = LibraryBrowserWindow(
        _handles(tmp_path, cache), thumbnail_cache=cache, on_pick=lambda _v: None,
    )
    try:
        window.show()
        ex_style = ctypes.windll.user32.GetWindowLongW(int(window.winId()), -20)  # GWL_EXSTYLE

        assert ex_style & 0x00000080, "WS_EX_TOOLWINDOW should be set"
        assert not (ex_style & 0x00040000), "WS_EX_APPWINDOW should NOT be set"
    finally:
        window.close()


def _double_click(window: LibraryBrowserWindow, row: int) -> None:
    """Deliver a genuine double-click onto *row*'s tile.

    The whole four-event sequence a mouse produces, sent to the viewport so it
    runs through ``QAbstractItemView`` exactly as the real gesture does.  The
    press and release are not decoration: a lone ``MouseButtonDblClick`` leaves
    the view with no pressed index and it discards the event, on ours and on a
    bare QListWidget alike.  ``QTest.mouseDClick`` is not used either — on this
    hidden desktop it delivers nothing to the view at all, so a test written on
    it would pass or fail on the harness rather than on the browser.
    """
    point = QPointF(window.grid.visualItemRect(window.grid.item(row)).center())
    for kind in (
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseButtonRelease,
    ):
        QApplication.sendEvent(window.grid.viewport(), QMouseEvent(
            kind, point, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        ))


def test_a_double_click_reaches_the_pick_and_ends_the_browse(tmp_path: Path):
    """The gesture, not the signal — and the whole way out of the process.

    A unit test can only emit ``itemActivated`` by hand, which says the handler
    is wired and nothing about whether the gesture ever gets there — and the
    gesture was never the half that was broken.  The browse *ended* nowhere: Qt
    does not count a Tool window towards the last-window quit, so a picked video
    sat in the result file with the bridge still blocked on a process that had
    nothing left to do, and from the outside a double-click looked like it did
    nothing.  So this drives the click through a running event loop that has to
    actually return.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    handles = _handles(tmp_path, cache)
    app = QApplication.instance()
    picked: list[str] = []
    window = LibraryBrowserWindow(
        handles, thumbnail_cache=cache, on_pick=picked.append, on_close=app.quit,
    )
    window.open_folder((SECTIONS[0],))
    window.resize(800, 500)
    window.show()
    app.processEvents()

    row = window.grid.rows.index(handles[0])
    QTimer.singleShot(0, lambda: _double_click(window, row))
    QTimer.singleShot(4000, app.quit)  # watchdog: never hang the suite
    app.exec()

    assert picked == [handles[0].video], "a double-click must play the video"
    assert window.isHidden(), "picking closes the browse"


def test_opening_a_folder_takes_a_double_click_too(tmp_path: Path):
    """The same gesture on a folder tile walks into it rather than playing it."""
    cache = tmp_path / "cache"
    cache.mkdir()
    window = LibraryBrowserWindow(
        _handles(tmp_path, cache), thumbnail_cache=cache, on_pick=lambda _v: None,
    )
    try:
        window.resize(800, 500)
        window.show()
        QApplication.instance().processEvents()

        _double_click(window, 0)

        assert window.windowTitle().endswith(SECTIONS[0])
    finally:
        window.close()
