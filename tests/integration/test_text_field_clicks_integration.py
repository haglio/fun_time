from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time

import pytest

from fun_time.text_field_clicks import LATE_S, TextFieldClicks
from fun_time.win32_events import PressAndCaretWatch
from fun_time.win32_loader import Win32Rect, load_dll
from tests.integration.throwaway_chrome import TAB_TIMEOUT_S, WINDOW_TIMEOUT_S, await_window

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="reads Windows' own reports of presses and carets"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

CHROME_MARKER = "FUNTIMEMARK-TEXT-FIELD-PAGE"
QT_MARKER = "FUNTIMEMARK-TEXT-FIELD-WINDOW"

CHROME_PAGE = f"""<style>
 body {{ margin: 0; font: 16px sans-serif }}
 #field {{ position: fixed; left: 0; top: 0; width: 45vw; height: 100vh }}
 #words {{ position: fixed; left: 55vw; top: 0; width: 45vw; height: 100vh; overflow: hidden }}
</style>
<textarea id=field>made-up words already in the field</textarea>
<div id=words>{"Made-up words that are not a field. " * 80}</div>
<script>
 document.addEventListener("focusin", e => document.title = "{CHROME_MARKER}-" + e.target.id);
 document.addEventListener("mousedown", e => {{
   if (e.target.id === "words") document.title = "{CHROME_MARKER}-words";
 }});
 addEventListener("load", () => requestAnimationFrame(() => requestAnimationFrame(
   () => document.title = "{CHROME_MARKER}-painted")));
</script>"""

QT_WINDOW = """
import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

marker = sys.argv[1]
app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle(marker)
window.resize(800, 300)
field = QLineEdit(window)
field.setGeometry(10, 10, 380, 40)
words = QLabel("Made-up words that are not a field. " * 6, window)
words.setGeometry(410, 10, 380, 200)
words.setWordWrap(True)
words.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
app.focusChanged.connect(
    lambda _old, new: window.setWindowTitle(f"{marker}-{type(new).__name__}"))
window.show()
app.exec()
"""

BEFORE_THE_PRESS_S = 0.05
HELD_FOR_S = 0.08
ONE_CLICKS_ANSWER_S = 3.0
LONG_ENOUGH_TO_BE_SURE_S = 3 * LATE_S

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

_user32 = load_dll("user32", use_last_error=True)
_user32.PostMessageW.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
_user32.GetClientRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(Win32Rect)]


def _client_size(hwnd: int) -> tuple[int, int]:
    rect = Win32Rect()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right, rect.lower


def _click(hwnd: int, x: int, y: int) -> None:
    at = (y & 0xFFFF) << 16 | (x & 0xFFFF)
    _user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, at)
    time.sleep(BEFORE_THE_PRESS_S)
    _user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, at)
    time.sleep(HELD_FOR_S)
    _user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, at)


def _click_until(hwnd: int, x: int, y: int, *, the_title_shows: str) -> int:
    deadline = time.monotonic() + WINDOW_TIMEOUT_S
    while time.monotonic() < deadline:
        _click(hwnd, x, y)
        if await_window(the_title_shows, ONE_CLICKS_ANSWER_S) == hwnd:
            return hwnd
    return 0


def _clicked_into(clicks: TextFieldClicks, watch: PressAndCaretWatch, *, within: float) -> int | None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        for seen in watch.take():
            clicks.saw(seen)
        window = clicks.clicked_into(now=time.monotonic())
        if window is not None:
            return window
        time.sleep(0.02)
    return None


@pytest.fixture
def watch():
    # The hidden desktop has no pointer to read, so every press counts as one
    # made with the text pointer here; which pointer it was is the unit tests'.
    watching = PressAndCaretWatch(text_pointer=lambda: True)
    watching.start()
    yield watching
    watching.stop()


def test_a_click_into_a_chrome_text_field_is_one_and_a_click_on_its_words_is_not(chrome, watch):
    page = chrome.open_page(CHROME_MARKER, CHROME_PAGE)
    clicks = TextFieldClicks(watched=lambda seen: seen.window == page)
    width, height = _client_size(page)
    assert await_window(f"{CHROME_MARKER}-painted", WINDOW_TIMEOUT_S) == page

    assert _click_until(page, width // 4, height * 3 // 4,
                        the_title_shows=f"{CHROME_MARKER}-field") == page
    assert _clicked_into(clicks, watch, within=TAB_TIMEOUT_S) == page

    assert _click_until(page, width * 3 // 4, height * 3 // 4,
                        the_title_shows=f"{CHROME_MARKER}-words") == page
    assert _clicked_into(clicks, watch, within=LONG_ENOUGH_TO_BE_SURE_S) is None


@pytest.fixture
def qt_window():
    environment = {name: value for name, value in os.environ.items() if name != "QT_QPA_PLATFORM"}
    app = subprocess.Popen([sys.executable, "-c", QT_WINDOW, QT_MARKER], env=environment,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        yield await_window(QT_MARKER, WINDOW_TIMEOUT_S)
    finally:
        app.kill()
        app.wait()


def test_a_click_into_a_qt_line_edit_is_one_and_a_click_on_its_words_is_not(qt_window, watch):
    clicks = TextFieldClicks(watched=lambda seen: seen.window == qt_window)

    assert _click_until(qt_window, 100, 30, the_title_shows=f"{QT_MARKER}-QLineEdit") == qt_window
    assert _clicked_into(clicks, watch, within=TAB_TIMEOUT_S) == qt_window

    assert _click_until(qt_window, 500, 50, the_title_shows=f"{QT_MARKER}-QLabel") == qt_window
    assert _clicked_into(clicks, watch, within=LONG_ENOUGH_TO_BE_SURE_S) is None
