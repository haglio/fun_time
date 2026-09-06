"""Real-window checks for the main player library browser.

The browser's whole value is what the user *reads* off it — the handle names
under the stills — and the unit suite cannot see that: it renders on Qt's
offscreen platform, where ``QFontDatabase.families()`` is empty and every glyph
comes out as tofu, the missing-character rectangle.  A grid of it passes every
assertion a unit test can make about it.  So the "does it actually paint the
titles" check lives here, on the native platform, where the fonts are real.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PyQt6.QtCore import QEvent, QPointF, Qt, QTimer
from PyQt6.QtGui import QFontDatabase, QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from fun_time.config import load_config
from fun_time.library_browser import WINDOW_TITLE, LibraryBrowserWindow, browse_library
from fun_time.library_handles import CLIPS_SUFFIX, LibraryHandle
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.thumbnail_cache import thumbnail_path
from fun_time.win32 import (
    close_window,
    iter_zorder,
    set_always_on_top,
    wait_for_window_by_title,
    windows_obscuring,
)
from fun_time.windows_bridge_dispatch_loop import keep_a_browse_on_top

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
    offscreen has no font at all and paints every character as tofu.  Section
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
            assert metrics.inFont(character), f"{character!r} would paint as tofu"

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


# A title no real window carries, so the stand-in below is found by nothing else.
STAND_IN_TITLE = "FUNTIMEMARK-MAIN-PLAYER"

# A whole interpreter, Qt, and a walk of the library stand between the launch
# and the window.  Generous rather than tuned: a cold run is the slow one, and
# the wait ends the moment the window is there.
_BROWSE_WINDOW_TIMEOUT_S = 90.0


def test_the_browse_opens_in_front_of_the_window_it_opens_over(tmp_path: Path, cfg_factory):
    """A real browse, launched the way the bridge launches it, ends up on top.

    The browse opens over the main player's own rect, and it used to come up
    BEHIND it: Windows refuses ``SetForegroundWindow`` to a process that
    neither owns the foreground nor took the last input, and the browse is a
    child the bridge starts while the player holds both — so Qt's own
    ``activateWindow`` did nothing, silently.

    What this covers is the opening path itself, which nothing covered before:
    the production launch, a real child process, a real window, and that window
    landing above one already sitting over the same rect.

    It does NOT catch the bug above, and was checked against a build without
    the fix to be sure of that: a hidden desktop has no foreground window,
    which is one of the cases the rule allows, so the activation is granted
    here either way.  Only a real display can show the refusal.
    """
    config = load_config(cfg_factory())
    library = config.paths.nau_library_dirs[0] / "batch_one"
    library.mkdir(parents=True, exist_ok=True)
    for name in ("alpha.mp4", "beta.mp4"):
        (library / name).write_bytes(b"\0" * 2048)
    # The manifest the session's own writer produces, so the browse reads its
    # library exactly as it does in a real session.  The interpreter is the
    # only input a test must supply: the fixture config names a stub .exe, and
    # this launch has to really run.
    manifest = write_windows_bridge_manifest(config)

    stand_in = QWidget(None)
    stand_in.setWindowTitle(STAND_IN_TITLE)
    stand_in.setGeometry(100, 100, 900, 700)
    stand_in.show()
    QApplication.instance().processEvents()
    stand_in_hwnd = int(stand_in.winId())
    # The shape the session is in when a browse starts: the player runs in the
    # topmost band and the bridge drops it out for the browse's duration.
    set_always_on_top(stand_in_hwnd, True)
    set_always_on_top(stand_in_hwnd, False)

    browsing = threading.Thread(
        target=lambda: browse_library(manifest, sys.executable, over=(100, 100, 900, 700)),
        daemon=True,
    )
    browsing.start()
    browse_hwnd = 0
    try:
        browse_hwnd = wait_for_window_by_title(WINDOW_TITLE, _BROWSE_WINDOW_TIMEOUT_S)
        assert browse_hwnd, "the browse never opened a window"

        stack = iter_zorder()
        assert windows_obscuring(browse_hwnd, stack) == [], "something covers the browse"
        covering = [w.hwnd for w in windows_obscuring(stand_in_hwnd, stack)]
        assert browse_hwnd in covering, "the browse did not come up in front"
    finally:
        if browse_hwnd:
            close_window(browse_hwnd)
        browsing.join(timeout=30)
        stand_in.close()


def test_the_bridge_finds_an_open_browse_and_puts_it_back_on_top(
    tmp_path: Path, cfg_factory,
):
    """The lookup and the promotion the OmniPause fix rests on, for real.

    A browse's own process owns no window — the interpreter a session launches
    is a venv's ``python.exe``, which spawns the one that opens it — so the
    first fix here looked the window up by the started pid, found nothing, and
    left the browse buried under the player exactly as before.  Only a real
    launch has a real process tree to get that wrong against, which is why this
    lives here.

    The stand-in going topmost is what leaving OmniPause does to the players:
    ``HWND_TOPMOST`` inserts at the top of the band, over a browse that is not
    in it.  Re-asserting the browse has to clear it again.
    """
    config = load_config(cfg_factory())
    library = config.paths.nau_library_dirs[0] / "batch_one"
    library.mkdir(parents=True, exist_ok=True)
    for name in ("alpha.mp4", "beta.mp4"):
        (library / name).write_bytes(b"\0" * 2048)
    manifest = write_windows_bridge_manifest(config)

    stand_in = QWidget(None)
    stand_in.setWindowTitle(STAND_IN_TITLE)
    stand_in.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    stand_in.setGeometry(100, 100, 900, 700)
    stand_in.show()
    QApplication.instance().processEvents()
    stand_in_hwnd = int(stand_in.winId())

    # The bridge's own runner shape: it holds the Popen while the browse is up,
    # and that object is what the promotion is handed.
    started: list[subprocess.Popen] = []

    def runner(command, **kwargs):
        process = subprocess.Popen(command, **kwargs)
        started.append(process)
        process.wait()

    browsing = threading.Thread(
        target=lambda: browse_library(
            manifest, sys.executable, over=(100, 100, 900, 700), runner=runner),
        daemon=True,
    )
    browsing.start()
    browse_hwnd = 0
    try:
        browse_hwnd = wait_for_window_by_title(WINDOW_TITLE, _BROWSE_WINDOW_TIMEOUT_S)
        assert browse_hwnd, "the browse never opened a window"

        set_always_on_top(stand_in_hwnd, True)
        covering = [w.hwnd for w in windows_obscuring(browse_hwnd, iter_zorder())]
        assert covering == [stand_in_hwnd], "the browse should be buried at this point"

        assert keep_a_browse_on_top(started[0]) == browse_hwnd

        assert windows_obscuring(browse_hwnd, iter_zorder()) == []
    finally:
        set_always_on_top(stand_in_hwnd, False)
        if browse_hwnd:
            close_window(browse_hwnd)
        browsing.join(timeout=30)
        stand_in.close()
