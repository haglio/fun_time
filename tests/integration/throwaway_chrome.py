"""A real Chrome in a user data directory of the test's own, for the RFB's tests.

The Random Favs Browser is a window of the user's own Chrome, so these tests
never touch that one: Chrome's singleton is keyed on the user data directory,
and a fresh directory under the run's tmp_path is a browser of its own.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import struct
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from fun_time.process_tree import kill_process_tree
from fun_time.shortcuts import Shortcut
from fun_time.win32 import _every_window, close_window, window_process
from fun_time.win32_loader import load_dll
from fun_time.win32_process import list_child_pids
from tests.integration.integration_support import await_window
from tests.integration.run_clock import Budget
from tests.scratch import remove_scratch

_CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)

WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
GUID_SESSION_DISPLAY_STATUS = uuid.UUID("2b84c20e-ad23-4ddf-93db-05ffbd7efca5")
POWER_MONITOR_OFF = 0
SMTO_ABORTIFHUNG = 0x0002
NOTICE_TIMEOUT_MS = 5000

_user32 = load_dll("user32", use_last_error=True)
_user32.SendMessageTimeoutW.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
    ctypes.wintypes.UINT, ctypes.wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
_user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t

# Measured under the flake gate's load: a new window has taken over 60s to
# appear, and a closed Chrome over 30s to exit.
WINDOW_TIMEOUT_S = 90.0
TAB_TIMEOUT_S = 30.0
EXIT_TIMEOUT_S = 90.0


def chrome_exe() -> Path:
    for candidate in _CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    pytest.skip("Chrome is not installed at either standard location")


class ThrowawayChrome:
    """One browser, and every window and tab the test hands it.

    ``close`` waits for that browser to exit, ends what it leaves running, and
    takes its profile away itself.  Under the flake gate's load the browser
    exits while its crash handler and utility processes run on for up to a
    minute and a half, the handler holding CrashpadMetrics.pma, and a scratch
    directory removed under them failed the test that owned it.  The Popen
    handle keeps the browser's pid from being reused, so its children are
    found by that pid alone.
    """

    def __init__(self, directory: Path) -> None:
        self._exe = chrome_exe()
        self._pages = directory / "pages"
        self._pages.mkdir()
        self._profile = directory / "chrome_user_data"
        self._arguments = [
            f"--user-data-dir={self._profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--mute-audio",
            "--disable-backgrounding-occluded-windows",
        ]
        self._browser: subprocess.Popen | None = None
        self._windows: list[int] = []

    def page_for(self, marker: str, body: str = "") -> str:
        path = self._pages / f"{marker}.html"
        path.write_text(f"<!doctype html><title>{marker}</title>{body or f'<h1>{marker}</h1>'}",
                        encoding="utf-8")
        return path.as_uri()


    def shortcut(self) -> Shortcut:
        return Shortcut(target=str(self._exe), work_dir=str(self._exe.parent),
                        arguments=subprocess.list2cmdline(self._arguments))

    def open_window(self, *markers: str) -> int:
        return self._open_window_on(markers[0], *(self.page_for(marker) for marker in markers))

    def open_page(self, marker: str, body: str) -> int:
        return self._open_window_on(marker, self.page_for(marker, body))

    def _open_window_on(self, marker: str, *pages: str) -> int:
        self._launch("--new-window", *pages)
        hwnd = await_window(marker, WINDOW_TIMEOUT_S)
        self._windows.append(hwnd)
        return hwnd

    def hand_over(self, marker: str) -> int:
        self._launch(self.page_for(marker))
        return await_window(marker, TAB_TIMEOUT_S)

    def hear_that_the_display_is_off(self) -> None:
        notice = ctypes.create_string_buffer(GUID_SESSION_DISPLAY_STATUS.bytes_le + struct.pack(
            "<II", ctypes.sizeof(ctypes.wintypes.DWORD), POWER_MONITOR_OFF))
        for hwnd in _every_window(lambda hwnd: window_process(hwnd) == self._browser.pid):
            _user32.SendMessageTimeoutW(hwnd, WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, ctypes.addressof(notice),
                                        SMTO_ABORTIFHUNG, NOTICE_TIMEOUT_MS, None)

    def close(self) -> None:
        for hwnd in self._windows:
            close_window(hwnd)
        if self._browser is None:
            return
        budget = Budget(EXIT_TIMEOUT_S)
        while self._browser.poll() is None and not budget.expired():
            time.sleep(0.2)
        if self._browser.poll() is None:
            kill_process_tree(self._browser.pid)
            self._browser.wait()
        for child in list_child_pids(self._browser.pid):
            kill_process_tree(child)
        self._remove_the_profile()

    def _launch(self, *arguments: str) -> None:
        launched = subprocess.Popen([str(self._exe), *self._arguments, *arguments])
        if self._browser is None:
            self._browser = launched

    def _remove_the_profile(self) -> None:
        budget = Budget(TAB_TIMEOUT_S)
        while self._profile.exists():
            try:
                remove_scratch(self._profile)
            except PermissionError:
                if budget.expired():
                    raise
                time.sleep(0.5)
