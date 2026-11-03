from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from fun_time import raising_the_loading_screen, transition_screen
from fun_time.loading_reveal import LoadingReveal
from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.overlay_progress import PROGRESS_FILENAME
from fun_time.raising_the_loading_screen import open_the_loading_screen
from fun_time.session_handoff import DESKTOP, keep_the_transition_screen, launch_transition_screen
from fun_time.win32 import find_window_for_process, is_window_topmost, show_own_window
from fun_time.windows_bridge_sequencer import (
    apply_topmost_bands,
)

from .above_the_loading_screen import AboveTheLoadingScreen
from .integration_support import (
    FunTimeIntegrationSession,
    await_window,
    build_integration_config,
    build_integration_temp_root,
    ended_within,
    joined_within,
    seen_within,
    this_checkouts_siblings,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.GetWindowThreadProcessId.restype = wt.DWORD
_user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
]
_user32.CreateWindowExW.restype = wt.HWND
_user32.DestroyWindow.argtypes = [wt.HWND]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
_user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.OpenThread.restype = wt.HANDLE
_kernel32.SuspendThread.argtypes = [wt.HANDLE]
_kernel32.ResumeThread.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
WS_EX_TOPMOST = 0x00000008
WM_QUIT = 0x0012
THREAD_SUSPEND_RESUME = 0x0002


def test_nothing_stays_over_the_loading_screen_long_enough_to_be_seen():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    watcher = AboveTheLoadingScreen()
    watcher.start()
    try:
        session.start(wait_seconds=180.0, env_overrides={
            "FUN_TIME_INTEGRATION_OVERLAYS": "1",
            "FUN_TIME_DISABLE_DASHBOARD": "0",
        })
        joined_within(watcher, 60.0)

        assert watcher.loading_screen_was_up, "the loading screen never appeared"
        assert len(watcher.samples) > 100, (
            f"only {len(watcher.samples)} samples: the cover was barely up, so "
            "this measured nothing"
        )
        assert not watcher.too_long(), (
            "these sat over the loading screen long enough to be drawn: "
            + "; ".join(watcher.too_long())
        )
    finally:
        session.stop()


class _Windows(threading.Thread):
    def __init__(self, *titles: str, style: int = WS_POPUP, ex_style: int = 0) -> None:
        super().__init__(daemon=True)
        self.hwnds: dict[str, int] = {}
        self._titles = titles
        self._style = style
        self._ex_style = ex_style
        self._thread_id = 0
        self._up = threading.Event()

    def __enter__(self) -> dict[str, int]:
        self.start()
        assert seen_within(self._up.is_set, seconds=5.0), "the stand-in windows never came up"
        return self.hwnds

    def __exit__(self, *_exc) -> None:
        _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        joined_within(self, 5.0)

    def run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        for title in self._titles:
            self.hwnds[title] = int(_user32.CreateWindowExW(
                self._ex_style, "STATIC", title, self._style | WS_VISIBLE,
                100, 100, 320, 240, None, None, None, None))
        self._up.set()
        msg = wt.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.DispatchMessageW(ctypes.byref(msg))
        for hwnd in self.hwnds.values():
            _user32.DestroyWindow(hwnd)


@contextmanager
def _a_real_loading_screen(progress_file: Path):
    progress_file.write_text("0/1|Positioning windows...", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "fun_time.loading_screen", str(progress_file)],
        cwd=Path(__file__).resolve().parents[2],
    )
    try:
        hwnd = await_window(LOADING_SCREEN_TITLE, 15.0, exact=True)
        assert hwnd, "the loading screen never put its window up"
        yield hwnd
    finally:
        progress_file.write_text("DONE", encoding="utf-8")
        if not ended_within(process, 10.0):
            process.kill()


@contextmanager
def _unable_to_run(hwnd: int):
    thread = _kernel32.OpenThread(
        THREAD_SUSPEND_RESUME, False, _user32.GetWindowThreadProcessId(hwnd, None))
    _kernel32.SuspendThread(thread)
    try:
        yield
    finally:
        _kernel32.ResumeThread(thread)
        _kernel32.CloseHandle(thread)


def _sampling(watcher: AboveTheLoadingScreen) -> None:
    watcher.start()
    assert seen_within(lambda: watcher.samples, seconds=5.0, every_s=0.005), (
        "the watcher never sampled the loading screen")


def test_an_app_banded_under_a_loading_screen_too_busy_to_answer_never_shows_through_it(tmp_path):
    watcher = AboveTheLoadingScreen(timeout_s=60.0)
    with _Windows("portrait", "landscape") as funestras:
        with _a_real_loading_screen(tmp_path / PROGRESS_FILENAME) as loading_screen:
            _sampling(watcher)
            with _unable_to_run(loading_screen):
                apply_topmost_bands(funestras, beneath=loading_screen)
        joined_within(watcher, 15.0)

        assert not watcher.too_long(), (
            "banded while the loading screen could not answer, these sat over it: "
            + "; ".join(watcher.too_long())
        )
        assert all(is_window_topmost(hwnd) for hwnd in funestras.values()), (
            "Fun Time never made it into the topmost band"
        )


class _ShowsItself:
    def __init__(self, hwnd: int) -> None:
        self._hwnd = hwnd

    def show(self) -> None:
        show_own_window(self._hwnd)


def test_the_panel_revealed_under_a_loading_screen_too_busy_to_answer_never_shows_through_it(tmp_path):
    progress_file = tmp_path / PROGRESS_FILENAME
    watcher = AboveTheLoadingScreen(timeout_s=60.0)
    with _Windows("panel", ex_style=WS_EX_TOPMOST) as windows:
        panel = windows["panel"]
        with _a_real_loading_screen(progress_file) as loading_screen:
            _sampling(watcher)
            reveal = LoadingReveal(tmp_path)
            assert reveal.deferred, "the panel should be waiting under the loading screen"
            reveal.attach(panel, _ShowsItself(panel))
            progress_file.write_text("1/1|Finalizing...", encoding="utf-8")
            with _unable_to_run(loading_screen):
                reveal.maybe_reveal()
                time.sleep(0.2)
            time.sleep(0.2)
        joined_within(watcher, 15.0)

        assert not watcher.too_long(), (
            "revealed while the loading screen could not answer, these sat over it: "
            + "; ".join(watcher.too_long())
        )
        assert is_window_topmost(panel), "the panel never got back into the band"


class _Launches:
    def __init__(self, popen) -> None:
        self._popen = popen
        self.loading_screen: subprocess.Popen | None = None

    def __call__(self, args, **kwargs):
        process = self._popen(args, **kwargs)
        if "fun_time.loading_screen" in args:
            self.loading_screen = process
        return process


class _TheWayBack(threading.Thread):
    def __init__(self, crossing: subprocess.Popen, launches: _Launches) -> None:
        super().__init__(daemon=True)
        self._crossing = crossing
        self._launches = launches
        self.stopped = threading.Event()
        self.bare_samples = 0
        self.loading_screen_seen = False

    def run(self) -> None:
        while not self.stopped.is_set():
            crossing = find_window_for_process(self._crossing.pid, transition_screen.WINDOW_TITLE)
            screen = self._launches.loading_screen
            loading = bool(screen and find_window_for_process(screen.pid, LOADING_SCREEN_TITLE))
            self.loading_screen_seen |= loading
            self.bare_samples += not (crossing or loading)
            time.sleep(0.005)


def _the_way_back(state_dir: Path, monkeypatch, *, waiting_s: float) -> _TheWayBack:
    monkeypatch.setattr(raising_the_loading_screen, "WINDOW_WAIT_S", 0.0)
    monkeypatch.setattr(raising_the_loading_screen, "WINDOW_WAIT_UNDER_A_CROSSING_S", waiting_s)
    launches = _Launches(subprocess.Popen)
    monkeypatch.setattr(subprocess, "Popen", launches)
    siblings = this_checkouts_siblings()
    crossing = launch_transition_screen(state_dir, DESKTOP, sibling_checkouts=siblings)
    stop_keeping_it_up = keep_the_transition_screen(state_dir)
    try:
        assert seen_within(lambda: find_window_for_process(
            crossing.pid, transition_screen.WINDOW_TITLE), seconds=60.0), (
            '"Returning to Fun Time..." never came up')
        watcher = _TheWayBack(crossing, launches)
        watcher.start()
        loading_screen = open_the_loading_screen(state_dir, show_overlays=True, sibling_checkouts=siblings)
        try:
            assert ended_within(crossing, 30.0), (
                '"Returning to Fun Time..." never handed over to the loading screen')
            assert seen_within(lambda: watcher.loading_screen_seen, seconds=60.0), (
                "the loading screen never came up")
        finally:
            watcher.stopped.set()
            joined_within(watcher, 5.0)
            loading_screen.take_it_down()
        return watcher
    finally:
        stop_keeping_it_up()
        if crossing.poll() is None:
            crossing.kill()


@pytest.mark.real_startup_waits
def test_the_way_back_from_vr_never_shows_the_desktop_before_a_late_loading_screen(
        tmp_path, monkeypatch):
    watcher = _the_way_back(tmp_path, monkeypatch, waiting_s=60.0)

    assert watcher.bare_samples == 0, (
        f'the desktop showed between "Returning to Fun Time..." and the loading '
        f"screen, in {watcher.bare_samples} samples")


@pytest.mark.real_startup_waits
def test_a_way_back_that_stops_waiting_first_shows_the_desktop(tmp_path, monkeypatch):
    watcher = _the_way_back(tmp_path, monkeypatch, waiting_s=0.0)

    assert watcher.bare_samples > 0, (
        '"Returning to Fun Time..." went before the loading screen was up, and no '
        "sample found the desktop between them")
