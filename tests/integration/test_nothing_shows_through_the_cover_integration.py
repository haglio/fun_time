"""Nothing shows through the loading cover for long enough to see.

His: "many things flash out for a split second from underneath the loading
screen's scrim."  Nothing keeps a topmost window above the OTHER topmost
windows — every raise a session makes while the cover is up (showing a player,
moving it onto its rect, promoting it into the band) inserts that window ABOVE
the cover, and Windows never tells a window it has been displaced.  The cover's
only defense is taking the top back, so how fast it does that IS how long a
window shows through it.  It used to do that once every 200ms, and only then,
which is a fifth of a second of a player visible through the scrim — a split
second, once per raise, and there is a raise for every window in the room.

So this watches the window immediately above the cover, at 2ms, for the whole
time the cover is up, and asks how LONG anything managed to stay there.  Not
whether anything ever got above it: a promotion and the cover's answer to it are
two SetWindowPos calls and the gap between them is real, so the only truthful
question is whether that gap is short enough that no frame is ever drawn from
it.  One display frame is 16.7ms at 60Hz; the budget here is two, and the
behavior this replaces would blow it by an order of magnitude.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from fun_time.loading_screen import WINDOW_TITLE as LOADING_SCREEN_TITLE
from fun_time.mode_plan import STARTUP_MAIN_MODE
from fun_time.overlay_progress import NullProgress
from fun_time.satellites_mode import ORIGENERATOR_MODE
from fun_time.win32 import find_window_by_title, is_window_topmost, wait_for_window_by_title
from fun_time.windows_bridge_sequencer import (
    _hold_the_cover_for_the_hosted_app,
    apply_topmost_bands,
)

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
_user32.GetWindow.restype = wt.HWND
_user32.IsWindowVisible.argtypes = [wt.HWND]
_user32.IsIconic.argtypes = [wt.HWND]
_user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
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
GW_HWNDPREV = 3
WS_POPUP, WS_VISIBLE, WS_OVERLAPPEDWINDOW = 0x80000000, 0x10000000, 0x00CF0000
WS_EX_TOPMOST = 0x00000008
SW_SHOWMINNOACTIVE = 7
WM_QUIT = 0x0012
THREAD_SUSPEND_RESUME = 0x0002

# How long a window may sit over the cover: two display frames at 60Hz.  The
# floor under this is one SetWindowPos — the cover cannot answer a promotion
# before the promotion has happened — so the number cannot be zero, and the
# 200ms it replaces is twelve times it.
VISIBLE_MS = 34.0

# Samples closer together than this belong to the same stay.  Comfortably above
# the 2ms poll and below the budget, so a stay is never split and two separate
# ones are never merged.
_SAME_STAY_MS = 20.0
_POLL_S = 0.002


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


class _AboveTheCover(threading.Thread):
    """Sample the window immediately above the cover for as long as it is up.

    Only the one directly above it, because that is one ``GetWindow`` call and
    can therefore run at 2ms — walking the whole z-order costs tens of
    milliseconds a sample, which is the very interval being measured.
    """

    def __init__(self, timeout_s: float = 240.0) -> None:
        super().__init__(daemon=True)
        self._timeout_s = timeout_s
        self.cover_was_up = False
        self.samples = 0
        self.seen: dict[tuple[int, str], list[float]] = {}

    def run(self) -> None:
        deadline = time.monotonic() + self._timeout_s
        cover = 0
        while time.monotonic() < deadline:
            cover = find_window_by_title(LOADING_SCREEN_TITLE, exact=True)
            if cover:
                break
            time.sleep(0.01)
        if not cover:
            return
        self.cover_was_up = True
        while time.monotonic() < deadline and _user32.IsWindowVisible(cover):
            self.samples += 1
            hwnd = _user32.GetWindow(cover, GW_HWNDPREV)
            while hwnd and not _user32.IsWindowVisible(hwnd):
                hwnd = _user32.GetWindow(hwnd, GW_HWNDPREV)
            if hwnd:
                key = (int(hwnd), _window_title(int(hwnd)))
                self.seen.setdefault(key, []).append(time.monotonic())
            time.sleep(_POLL_S)

    def stays(self) -> list[tuple[str, float]]:
        """Each unbroken stay above the cover, as (what it was, how long in ms)."""
        out: list[tuple[str, float]] = []
        for (hwnd, title), stamps in self.seen.items():
            start = previous = stamps[0]
            for stamp in stamps[1:]:
                if (stamp - previous) * 1000 > _SAME_STAY_MS:
                    out.append((f"{title!r} (hwnd={hwnd})", (previous - start) * 1000))
                    start = stamp
                previous = stamp
            out.append((f"{title!r} (hwnd={hwnd})", (previous - start) * 1000))
        return out

    def too_long(self) -> list[str]:
        return [f"{what} for {ms:.0f}ms" for what, ms in self.stays() if ms > VISIBLE_MS]


def test_nothing_stays_over_the_cover_long_enough_to_be_seen():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    watcher = _AboveTheCover()
    watcher.start()
    try:
        # With the dashboard, because it and its notice overlay are two of the
        # topmost windows that can land over the cover.  Faked side-by-side
        # monitors for the reason the other overlay tests fake them: on the
        # hidden desktop's single screen the real layout collapses every window
        # onto it.
        session.start(wait_seconds=180.0, env_overrides={
            "FUN_TIME_INTEGRATION_OVERLAYS": "1",
            "FUN_TIME_DISABLE_DASHBOARD": "0",
            "FUN_TIME_FAKE_MONITORS": "0,0,1280,720;1280,0,720,1440",
        })
        watcher.join(timeout=60.0)

        assert watcher.cover_was_up, "the cover never appeared"
        assert watcher.samples > 100, (
            f"only {watcher.samples} samples: the cover was barely up, so this "
            "measured nothing"
        )
        assert not watcher.too_long(), (
            "these sat over the cover long enough to be drawn: "
            + "; ".join(watcher.too_long())
        )
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


class _Windows(threading.Thread):
    """Windows on a thread of their own that answers, as a player's own does."""

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
        assert self._up.wait(5.0), "the stand-in windows never came up"
        return self.hwnds

    def __exit__(self, *_exc) -> None:
        _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.join(timeout=5.0)

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
        hwnd = wait_for_window_by_title(LOADING_SCREEN_TITLE, timeout_s=15.0, exact=True)
        assert hwnd, "the loading screen never put its window up"
        yield hwnd
    finally:
        progress_file.write_text("DONE", encoding="utf-8")
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
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


def _sampling(watcher: _AboveTheCover) -> None:
    watcher.start()
    deadline = time.monotonic() + 5.0
    while not watcher.samples and time.monotonic() < deadline:
        time.sleep(0.005)
    assert watcher.samples, "the watcher never sampled the cover"


def test_a_room_banded_under_a_cover_too_busy_to_answer_never_shows_through_it(tmp_path):
    watcher = _AboveTheCover(timeout_s=60.0)
    with _Windows("portrait", "landscape") as players:
        with _a_real_cover(tmp_path / "startup_progress.txt") as cover:
            _sampling(watcher)
            with _unable_to_run(cover):
                apply_topmost_bands(players, STARTUP_MAIN_MODE, beneath=cover)
        watcher.join(timeout=15.0)

        assert not watcher.too_long(), (
            "banded while the cover could not answer, these sat over it: "
            + "; ".join(watcher.too_long())
        )
        assert all(is_window_topmost(hwnd) for hwnd in players.values()), (
            "the room never made it into the topmost band"
        )


def test_a_parked_hosted_window_restored_under_a_cover_too_busy_to_answer_never_shows_through_it(
        tmp_path):
    status_file = tmp_path / "origenerator_status.txt"
    status_file.write_text("portrait_active=1\nlandscape_active=1\n", encoding="utf-8")
    manifest = SimpleNamespace(
        commands=SimpleNamespace(origenerator_status_file=str(status_file)))
    core = SimpleNamespace(origenerator_pid=os.getpid(), satellites_mode=ORIGENERATOR_MODE)
    watcher = _AboveTheCover(timeout_s=60.0)
    with _Windows("Origenerator", style=WS_OVERLAPPEDWINDOW, ex_style=WS_EX_TOPMOST) as hosted:
        _user32.ShowWindow(hosted["Origenerator"], SW_SHOWMINNOACTIVE)
        with _a_real_cover(tmp_path / "startup_progress.txt") as cover:
            _sampling(watcher)
            with _unable_to_run(cover):
                restored = _hold_the_cover_for_the_hosted_app(
                    manifest, core=core, progress=NullProgress())
        watcher.join(timeout=15.0)

        assert restored == hosted["Origenerator"]
        assert not _user32.IsIconic(restored), "the hosted window was left parked"
        assert not watcher.too_long(), (
            "restored while the cover could not answer, these sat over it: "
            + "; ".join(watcher.too_long())
        )
