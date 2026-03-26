"""Win32 window operations for the Python orchestrator.

Wraps ctypes calls for window manipulation that the startup sequencer
needs: find/wait for windows by PID, move, set topmost, activate, query size.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

_user32 = ctypes.windll.user32  # type: ignore[attr-defined]

# Constants
SW_RESTORE = 9
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2


WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.BOOL,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)


def find_window_by_pid(pid: int) -> int:
    """Find a visible top-level window belonging to *pid*. Returns 0 if not found.

    Matches AHK's ``DetectHiddenWindows False`` behavior: only considers
    windows that are visible (``IsWindowVisible``) and have a non-empty title.
    This avoids grabbing internal surfaces like Direct3D rendering windows.
    """
    best: int = 0

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal best
        window_pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid:
            return True
        if not _user32.IsWindowVisible(hwnd):
            return True
        # Check for non-empty title (skip internal/unnamed windows)
        title_len = _user32.GetWindowTextLengthW(hwnd)
        if title_len <= 0:
            return True
        best = hwnd
        return False  # stop enumeration

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return best


def wait_for_window(pid: int, timeout_s: float = 15.0) -> int:
    """Poll for a window belonging to *pid*, returning its hwnd or 0 on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hwnd = find_window_by_pid(pid)
        if hwnd:
            return hwnd
        time.sleep(0.1)
    return 0


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    """Restore and reposition a window (WinRestore + WinMove equivalent)."""
    _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


def set_always_on_top(hwnd: int, on_top: bool) -> None:
    """Set or clear the always-on-top flag for a window."""
    insert_after = HWND_TOPMOST if on_top else HWND_NOTOPMOST
    _user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def activate_window(hwnd: int) -> None:
    """Bring a window to the foreground."""
    _user32.SetForegroundWindow(hwnd)


def find_window_by_title(title: str) -> int:
    """Find a visible window whose title contains *title*. Returns 0 if not found."""
    best: int = 0
    buf = ctypes.create_unicode_buffer(256)

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal best
        if not _user32.IsWindowVisible(hwnd):
            return True
        _user32.GetWindowTextW(hwnd, buf, 256)
        if title in buf.value:
            best = hwnd
            return False
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return best


SW_SHOW = 5
SW_HIDE = 0


def show_window(hwnd: int) -> None:
    """Show a window (WinShow equivalent)."""
    _user32.ShowWindow(hwnd, SW_SHOW)


def hide_window(hwnd: int) -> None:
    """Hide a window (WinHide equivalent)."""
    _user32.ShowWindow(hwnd, SW_HIDE)


def send_key_to_window(hwnd: int, key: str) -> None:
    """Send a single character keystroke to a window via PostMessage."""
    WM_CHAR = 0x0102
    for ch in key:
        _user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) for a window."""
    rect = ctypes.wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
