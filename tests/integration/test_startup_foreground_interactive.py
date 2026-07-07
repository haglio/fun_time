"""Interactive-only: session startup must not steal the user's real foreground.

This is the one integration test that cannot run on the hidden desktop the rest of
the suite uses — it asserts about the *input desktop's* foreground window, which a
non-input desktop has none of.  It lives in its own file so the hidden-desktop
runner (``tests/integration/hidden_desktop.py``) can ``--ignore`` it; run it by hand
on your real desktop when you touch the startup focus-handling code:

    FUN_TIME_RUN_INTEGRATION=1 .venv/Scripts/python.exe -m pytest \
        tests/integration/test_startup_foreground_interactive.py
"""
from __future__ import annotations

import contextlib
import ctypes
import shutil
import sys

import pytest

from fun_time.win32 import find_window_by_pid, get_foreground_window

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)


@contextlib.contextmanager
def _foreground_sentinel():
    """Create a tiny popup window and make it the foreground window.

    Gives focus-stealing tests a deterministic foreground to verify.
    The production code saves the foreground hwnd in integration mode
    and restores it after minimizing VLC windows.

    Uses the Alt-key trick to gain foreground activation privilege —
    without it, SetForegroundWindow fails from a background process
    (e.g. pytest running under a terminal).
    """
    _user32 = ctypes.windll.user32
    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    hwnd = _user32.CreateWindowExW(
        0, "Static", "FocusSentinel",
        WS_POPUP | WS_VISIBLE,
        0, 0, 1, 1,
        0, 0, 0, 0,
    )
    assert hwnd, "Failed to create sentinel window"
    try:
        # Press/release Alt to gain foreground activation privilege.
        VK_MENU = 0x12
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        _user32.SetForegroundWindow(hwnd)
        yield hwnd
    finally:
        _user32.DestroyWindow(hwnd)


def test_fun_time_startup_does_not_steal_foreground():
    """Session startup must not steal the user's foreground window.

    Creates a sentinel window as a deterministic foreground target so the
    production code's save/restore cycle has a known hwnd to work with.
    The lock held during startup + minimize prevents VLC's Qt from calling
    SetForegroundWindow; after unlock the restore puts the sentinel back.
    """
    with _foreground_sentinel() as sentinel_hwnd:
        assert get_foreground_window() == sentinel_hwnd, (
            "Sentinel failed to become foreground — test environment issue"
        )

        temp_root = build_integration_temp_root()
        config_path = build_integration_config(temp_root)
        session = FunTimeIntegrationSession(config_path)
        try:
            session.start()

            fg_hwnd = get_foreground_window()
            child_pids = session.read_child_pids()
            for name, pid in child_pids.items():
                if not pid:
                    continue
                child_hwnd = find_window_by_pid(pid)
                if child_hwnd:
                    assert child_hwnd != fg_hwnd, (
                        f"Foreground stolen by {name} (pid={pid}, hwnd={child_hwnd})"
                    )
        finally:
            session.stop()
            shutil.rmtree(temp_root, ignore_errors=True)
