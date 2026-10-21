"""Nothing shows through the loading cover for long enough to see.

His: "many things flash out for a split second from underneath the loading
screen's scrim."  Nothing keeps a topmost window above the OTHER topmost
windows — every raise a session makes while the cover is up (showing a Funestra,
moving it onto its rect, promoting it into the band) inserts that window ABOVE
the cover, and Windows never tells a window it has been displaced.

The cover answers by taking the top back every 16ms, which is what bounds a
flash on an idle machine — measured at 27ms for one raise, against the 200ms
the cover's first defense took.  It is no bound at all on a busy one: with
twice as many busy processes as the machine has cores, ten of twenty-four
raises sat over the cover past the budget and the worst sat there for 198ms.
So a window that must not flash is never put above the cover in the first
place — the session's promotions land under it (``set_always_on_top(under=)``)
and the panel leaves the topmost band before it becomes visible
(:mod:`fun_time.loading_reveal`) — and the cover's own poll is the backstop
for whatever nobody thought of.

This watches the window immediately above the cover, at 2ms, for the whole
time the cover is up, and asks how LONG anything managed to stay there.  Not
whether anything ever got above it: a raise and the answer to it are two
SetWindowPos calls and the gap between them is real, so the only truthful
question is whether that gap is short enough that no frame is ever drawn from
it.  :mod:`above_the_cover` holds the budget and the arithmetic.
"""
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

from fun_time import loading_cover, transition_screen
from fun_time.loading_cover import open_the_cover
from fun_time.loading_reveal import LoadingReveal
from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.overlay_progress import PROGRESS_FILENAME
from fun_time.session_handoff import DESKTOP, keep_the_crossing_cover, launch_crossing_cover
from fun_time.win32 import find_window_for_process, is_window_topmost, show_own_window
from fun_time.windows_bridge_sequencer import (
    apply_topmost_bands,
)

from .above_the_cover import AboveTheCover
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


def test_nothing_stays_over_the_cover_long_enough_to_be_seen():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    watcher = AboveTheCover()
    watcher.start()
    try:
        # With the dashboard, because it and its notice overlay are two of the
        # topmost windows that can land over the cover.
        session.start(wait_seconds=180.0, env_overrides={
            "FUN_TIME_INTEGRATION_OVERLAYS": "1",
            "FUN_TIME_DISABLE_DASHBOARD": "0",
        })
        joined_within(watcher, 60.0)

        assert watcher.cover_was_up, "the cover never appeared"
        assert len(watcher.samples) > 100, (
            f"only {len(watcher.samples)} samples: the cover was barely up, so "
            "this measured nothing"
        )
        assert not watcher.too_long(), (
            "these sat over the cover long enough to be drawn: "
            + "; ".join(watcher.too_long())
        )
    finally:
        session.stop()


class _Windows(threading.Thread):
    """Windows on a thread of their own that answers, as a Funestra's own does."""

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
def _a_real_cover(progress_file: Path):
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
    """The thread that owns *hwnd*, suspended: to everything that needs it to
    answer, a thread starved by a loaded machine looks exactly like this."""
    thread = _kernel32.OpenThread(
        THREAD_SUSPEND_RESUME, False, _user32.GetWindowThreadProcessId(hwnd, None))
    _kernel32.SuspendThread(thread)
    try:
        yield
    finally:
        _kernel32.ResumeThread(thread)
        _kernel32.CloseHandle(thread)


def _sampling(watcher: AboveTheCover) -> None:
    watcher.start()
    assert seen_within(lambda: watcher.samples, seconds=5.0, every_s=0.005), (
        "the watcher never sampled the cover")


def test_a_room_banded_under_a_cover_too_busy_to_answer_never_shows_through_it(tmp_path):
    watcher = AboveTheCover(timeout_s=60.0)
    with _Windows("portrait", "landscape") as funestras:
        with _a_real_cover(tmp_path / PROGRESS_FILENAME) as cover:
            _sampling(watcher)
            with _unable_to_run(cover):
                apply_topmost_bands(funestras, beneath=cover)
        joined_within(watcher, 15.0)

        assert not watcher.too_long(), (
            "banded while the cover could not answer, these sat over it: "
            + "; ".join(watcher.too_long())
        )
        assert all(is_window_topmost(hwnd) for hwnd in funestras.values()), (
            "the room never made it into the topmost band"
        )


class _ShowsItself:
    """What the reveal is handed: a window whose ``show`` makes it visible.

    The panel's own ``show`` is Qt's, which ends in the same ShowWindow — and
    ShowWindow is what puts a topmost window at the top of the band.
    """

    def __init__(self, hwnd: int) -> None:
        self._hwnd = hwnd

    def show(self) -> None:
        show_own_window(self._hwnd)


def test_the_panel_revealed_under_a_cover_too_busy_to_answer_never_shows_through_it(tmp_path):
    """The panel comes out from under the cover one phase before the cover goes,
    and it is topmost, so showing it used to put it at the top of the band —
    over the cover — until the call after it put it back.  That gap is whatever
    the machine gives the two processes: 68ms of the panel drawn through the
    scrim in one loaded run, and here the cover cannot answer at all.
    """
    progress_file = tmp_path / PROGRESS_FILENAME
    watcher = AboveTheCover(timeout_s=60.0)
    with _Windows("panel", ex_style=WS_EX_TOPMOST) as windows:
        panel = windows["panel"]
        with _a_real_cover(progress_file) as cover:
            _sampling(watcher)
            reveal = LoadingReveal(tmp_path)
            assert reveal.deferred, "the panel should be waiting under the cover"
            reveal.attach(panel, _ShowsItself(panel))
            progress_file.write_text("1/1|Finalizing...", encoding="utf-8")
            with _unable_to_run(cover):
                reveal.maybe_reveal()
                time.sleep(0.2)
            time.sleep(0.2)
        joined_within(watcher, 15.0)

        assert not watcher.too_long(), (
            "revealed while the cover could not answer, these sat over it: "
            + "; ".join(watcher.too_long())
        )
        # Inside the block that owns the window: a destroyed one reads as not
        # topmost, so an assertion after it passes whatever the reveal did.
        assert is_window_topmost(panel), "the panel never got back into the band"


class _Launches:
    """``subprocess.Popen``, keeping hold of the loading screen it starts."""

    def __init__(self, popen) -> None:
        self._popen = popen
        self.loading_screen: subprocess.Popen | None = None

    def __call__(self, args, **kwargs):
        process = self._popen(args, **kwargs)
        if "fun_time.loading_screen" in args:
            self.loading_screen = process
        return process


class _TheWayBack(threading.Thread):
    """Which of "Returning to Fun Time..." and the loading screen can be seen,
    sampled until stopped.  The first is looked at first, so a sample that finds
    neither means it went before the loading screen was up -- never that both
    changed between the two looks."""

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
    """A launch from "Returning to Fun Time..." that waits *waiting_s* for its
    loading screen, with the launch's usual wait set to none at all -- so every
    loading screen is later than that -- watched until both have been seen."""
    monkeypatch.setattr(loading_cover, "WINDOW_WAIT_S", 0.0)
    monkeypatch.setattr(loading_cover, "WINDOW_WAIT_UNDER_A_CROSSING_S", waiting_s)
    launches = _Launches(subprocess.Popen)
    monkeypatch.setattr(subprocess, "Popen", launches)
    siblings = this_checkouts_siblings()
    crossing = launch_crossing_cover(state_dir, DESKTOP, sibling_checkouts=siblings)
    stop_keeping_it_up = keep_the_crossing_cover(state_dir)
    try:
        assert seen_within(lambda: find_window_for_process(
            crossing.pid, transition_screen.WINDOW_TITLE), seconds=60.0), (
            '"Returning to Fun Time..." never came up')
        watcher = _TheWayBack(crossing, launches)
        watcher.start()
        cover = open_the_cover(state_dir, show_overlays=True, sibling_checkouts=siblings)
        try:
            assert ended_within(crossing, 30.0), (
                '"Returning to Fun Time..." never handed over to the loading screen')
            assert seen_within(lambda: watcher.loading_screen_seen, seconds=60.0), (
                "the loading screen never came up")
        finally:
            watcher.stopped.set()
            joined_within(watcher, 5.0)
            cover.take_it_down()
        return watcher
    finally:
        stop_keeping_it_up()
        if crossing.poll() is None:
            crossing.kill()


@pytest.mark.real_startup_waits
def test_the_way_back_from_vr_never_shows_the_desktop_before_a_late_loading_screen(
        tmp_path, monkeypatch):
    """"Returning to Fun Time..." stands until the loading screen is up to take
    over from it.  The launch used to give the loading screen a few seconds and
    take "Returning to Fun Time..." down whether or not it had come, so on a busy
    computer the desktop showed between the two."""
    watcher = _the_way_back(tmp_path, monkeypatch, waiting_s=60.0)

    assert watcher.bare_samples == 0, (
        f'the desktop showed between "Returning to Fun Time..." and the loading '
        f"screen, in {watcher.bare_samples} samples")


@pytest.mark.real_startup_waits
def test_a_way_back_that_stops_waiting_first_shows_the_desktop(tmp_path, monkeypatch):
    """The old hand-over, run so the check above is known to see what it rules
    out: a sample finding neither screen is a sample of the bare desktop."""
    watcher = _the_way_back(tmp_path, monkeypatch, waiting_s=0.0)

    assert watcher.bare_samples > 0, (
        '"Returning to Fun Time..." went before the loading screen was up, and no '
        "sample found the desktop between them")
