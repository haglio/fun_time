"""A real Chrome in a user data directory of the test's own, for the RFB's tests.

The Random Favs Browser is a window of the user's own Chrome, so these tests
never touch that one: Chrome's singleton is keyed on the user data directory,
and a fresh directory under the run's tmp_path is a browser of its own.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from fun_time.shortcuts import Shortcut
from fun_time.win32 import close_window, find_window_by_title
from fun_time.win32_process import list_child_pids
from fun_time.windows_bridge_orchestrator import kill_process_tree
from tests.scratch import remove_scratch

_CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)

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


def await_window(marker: str, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_window_by_title(marker)
        if hwnd:
            return hwnd
        time.sleep(0.2)
    return 0


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
        ]
        self._browser: subprocess.Popen | None = None
        self._windows: list[int] = []

    def page_for(self, marker: str) -> str:
        path = self._pages / f"{marker}.html"
        path.write_text(f"<!doctype html><title>{marker}</title><h1>{marker}</h1>", encoding="utf-8")
        return path.as_uri()

    def shortcut(self) -> Shortcut:
        return Shortcut(target=str(self._exe), work_dir=str(self._exe.parent),
                        arguments=subprocess.list2cmdline(self._arguments))

    def open_window(self, *markers: str) -> int:
        self._launch("--new-window", *(self.page_for(marker) for marker in markers))
        hwnd = await_window(markers[0], WINDOW_TIMEOUT_S)
        self._windows.append(hwnd)
        return hwnd

    def hand_over(self, marker: str) -> int:
        self._launch(self.page_for(marker))
        return await_window(marker, TAB_TIMEOUT_S)

    def close(self) -> None:
        for hwnd in self._windows:
            close_window(hwnd)
        if self._browser is None:
            return
        try:
            self._browser.wait(timeout=EXIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
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
        deadline = time.monotonic() + TAB_TIMEOUT_S
        while self._profile.exists():
            try:
                remove_scratch(self._profile)
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
