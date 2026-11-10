from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from fun_time.config import load_config
from fun_time.library_browser import (
    TOP_LEVEL_NAME,
    WINDOW_TITLE,
    BrowseCallbacks,
    BrowseOpening,
    LibraryBrowserWindow,
    browse_library,
)
from fun_time.library_handles import LibraryHandle
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.thumbnail_cache import thumbnail_path
from fun_time.win32 import (
    GWL_EXSTYLE,
    WS_EX_APPWINDOW,
    WS_EX_TOOLWINDOW,
    close_window,
    iter_zorder,
    set_always_on_top,
    windows_obscuring,
)
from fun_time.windows_bridge_dispatch_loop import raise_a_browse_the_restack_left_underneath
from tests.integration.integration_support import await_window, joined_within, wait_for
from tests.mouse_events import a_double_click_as_the_mouse_sends_it

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="paints a real Qt window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

TITLES = ("Alpha Studio - Scene One", "Beta Collective - The Long Afternoon 2")
SECTIONS = ("big_batch", "small_batch")


def _handles(tmp_path: Path, cache: Path) -> list[LibraryHandle]:
    handles = []
    for index, title in enumerate(TITLES):
        video = tmp_path / f"v{index}.mp4"
        video.write_bytes(b"\0" * (100 + index))
        image = Image.new("RGB", (176, 99), (40, 60, 90))
        ImageDraw.Draw(image).rectangle((10, 10, 60, 60), fill=(200, 120, 40))
        image.save(thumbnail_path(video, cache), "JPEG")
        handles.append(
            LibraryHandle(title=title, versions=(str(video),), section=SECTIONS[index])
        )
    return handles


def test_the_grid_paints_its_titles_and_stills(tmp_path: Path, real_fonts_where_offscreen_paints_tofu):
    cache = tmp_path / "cache"
    cache.mkdir()
    handles = _handles(tmp_path, cache)

    window = LibraryBrowserWindow(
        handles, thumbnail_cache=cache, callbacks=BrowseCallbacks(on_pick=lambda _v: None))
    try:
        window.setGeometry(0, 0, 600, 300)
        window.show()
        painted = window.grab().toImage()

        metrics = window.grid.fontMetrics()
        assert metrics.horizontalAdvance(TITLES[0]) > 0
        for character in set("".join(TITLES + SECTIONS)):
            assert metrics.inFont(character), f"{character!r} would paint as tofu"

        tiles = [row for row, handle in enumerate(window.grid.rows) if handle is not None]
        assert not window.grid.item(tiles[0]).icon().isNull()
        assert painted.width() > 0
        assert painted.height() > 0
        colors = {painted.pixel(x, y) for x in range(0, painted.width(), 7)
                  for y in range(0, painted.height(), 7)}
        assert len(colors) > 3, "the grid painted a flat slab, not tiles"
    finally:
        window.close()


def test_the_browser_window_owns_no_taskbar_button(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    window = LibraryBrowserWindow(
        _handles(tmp_path, cache), thumbnail_cache=cache,
        callbacks=BrowseCallbacks(on_pick=lambda _v: None),
    )
    try:
        window.show()
        ex_style = ctypes.windll.user32.GetWindowLongW(int(window.winId()), GWL_EXSTYLE)

        assert ex_style & WS_EX_TOOLWINDOW, "WS_EX_TOOLWINDOW should be set"
        assert not (ex_style & WS_EX_APPWINDOW), "WS_EX_APPWINDOW should NOT be set"
    finally:
        window.close()


def test_a_double_click_reaches_the_pick_and_ends_the_browse(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    handles = _handles(tmp_path, cache)
    app = QApplication.instance()
    picked: list[str] = []
    window = LibraryBrowserWindow(
        handles, thumbnail_cache=cache,
        callbacks=BrowseCallbacks(on_pick=picked.append, on_close=app.quit),
    )
    window.open_folder((SECTIONS[0],))
    window.resize(800, 500)
    window.show()
    app.processEvents()

    row = window.grid.rows.index(handles[0])
    QTimer.singleShot(0, lambda: a_double_click_as_the_mouse_sends_it(window.grid, row))
    QTimer.singleShot(4000, app.quit)
    app.exec()

    assert picked == [handles[0].video], "a double-click must play the video"
    assert window.isHidden(), "picking closes the browse"


def test_opening_a_folder_takes_a_double_click_too(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    window = LibraryBrowserWindow(
        _handles(tmp_path, cache), thumbnail_cache=cache,
        callbacks=BrowseCallbacks(on_pick=lambda _v: None),
    )
    try:
        window.resize(800, 500)
        window.show()
        QApplication.instance().processEvents()

        a_double_click_as_the_mouse_sends_it(window.grid, 0)

        words = window.header.plain_text()
        assert words == f"{TOP_LEVEL_NAME} / {SECTIONS[0]}"
    finally:
        window.close()


STAND_IN_TITLE = "FUNTIMEMARK-MAIN-FUNESTRA"

_A_REAL_INTERPRETER = sys.executable

# A whole interpreter, Qt, and a walk of the library stand between the launch
# and the window.  Generous rather than tuned: a cold run is the slow one, and
# the wait ends the moment the window is there.
_BROWSE_WINDOW_TIMEOUT_S = 90.0


def _until_no_browse_is_open() -> None:
    wait_for(lambda: not any(window.title == WINDOW_TITLE for window in iter_zorder()),
             desc="the browse an earlier test opened to finish closing")


def _dropped_from_the_band_as_the_bridge_does_for_a_browse(hwnd: int) -> None:
    set_always_on_top(hwnd, True)
    set_always_on_top(hwnd, False)


def test_the_browse_opens_in_front_where_nothing_holds_the_foreground(tmp_path: Path, cfg_factory):
    config = load_config(cfg_factory())
    library = config.paths.main_funestra_library_dirs[0] / "batch_one"
    library.mkdir(parents=True, exist_ok=True)
    for name in ("alpha.mp4", "beta.mp4"):
        (library / name).write_bytes(b"\0" * 2048)
    manifest = write_windows_bridge_manifest(config)
    _until_no_browse_is_open()

    stand_in = QWidget(None)
    stand_in.setWindowTitle(STAND_IN_TITLE)
    stand_in.setGeometry(100, 100, 900, 700)
    stand_in.show()
    QApplication.instance().processEvents()
    stand_in_hwnd = int(stand_in.winId())
    _dropped_from_the_band_as_the_bridge_does_for_a_browse(stand_in_hwnd)

    browsing = threading.Thread(
        target=lambda: browse_library(
            manifest, _A_REAL_INTERPRETER, BrowseOpening(over=(100, 100, 900, 700))),
        daemon=True,
    )
    browsing.start()
    browse_hwnd = 0
    try:
        browse_hwnd = await_window(WINDOW_TITLE, _BROWSE_WINDOW_TIMEOUT_S)
        assert browse_hwnd, "the browse never opened a window"

        stack = iter_zorder()
        assert windows_obscuring(browse_hwnd, stack) == [], "something covers the browse"
        covering = [w.hwnd for w in windows_obscuring(stand_in_hwnd, stack)]
        assert browse_hwnd in covering, "the browse did not come up in front"
    finally:
        if browse_hwnd:
            close_window(browse_hwnd)
        joined_within(browsing, 30)
        stand_in.close()


def test_the_bridge_finds_an_open_browse_and_puts_it_back_on_top(
    tmp_path: Path, cfg_factory,
):
    config = load_config(cfg_factory())
    library = config.paths.main_funestra_library_dirs[0] / "batch_one"
    library.mkdir(parents=True, exist_ok=True)
    for name in ("alpha.mp4", "beta.mp4"):
        (library / name).write_bytes(b"\0" * 2048)
    manifest = write_windows_bridge_manifest(config)
    _until_no_browse_is_open()

    stand_in = QWidget(None)
    stand_in.setWindowTitle(STAND_IN_TITLE)
    stand_in.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    stand_in.setGeometry(100, 100, 900, 700)
    stand_in.show()
    QApplication.instance().processEvents()
    stand_in_hwnd = int(stand_in.winId())

    started: list[subprocess.Popen] = []

    def runner(command, **kwargs):
        process = subprocess.Popen(command, **kwargs)
        started.append(process)
        process.wait()

    browsing = threading.Thread(
        target=lambda: browse_library(
            manifest, _A_REAL_INTERPRETER,
            BrowseOpening(over=(100, 100, 900, 700)), runner=runner),
        daemon=True,
    )
    browsing.start()
    browse_hwnd = 0
    try:
        browse_hwnd = await_window(WINDOW_TITLE, _BROWSE_WINDOW_TIMEOUT_S)
        assert browse_hwnd, "the browse never opened a window"

        set_always_on_top(stand_in_hwnd, True)
        covering = [w.hwnd for w in windows_obscuring(browse_hwnd, iter_zorder())]
        assert covering == [stand_in_hwnd], "the browse should be buried at this point"

        wait_for(lambda: started, desc="the browse's process to be handed to the runner")
        assert raise_a_browse_the_restack_left_underneath(started[0]) == browse_hwnd

        def browse_uncovered() -> bool:
            QApplication.processEvents()
            return not windows_obscuring(browse_hwnd, iter_zorder())

        wait_for(browse_uncovered,
                 desc="the browse to come back above the stand-in, however late its window answers")
    finally:
        set_always_on_top(stand_in_hwnd, False)
        if browse_hwnd:
            close_window(browse_hwnd)
        joined_within(browsing, 30)
        stand_in.close()
