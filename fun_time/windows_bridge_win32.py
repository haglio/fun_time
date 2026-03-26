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
HWND_TOPMOST = ctypes.wintypes.HWND(-1)
HWND_NOTOPMOST = ctypes.wintypes.HWND(-2)

# Declare argtypes so ctypes passes HWND parameters as 64-bit pointers.
# Without this, ctypes defaults to c_int (32-bit) for Python ints, which
# corrupts the sentinel HWND_TOPMOST/HWND_NOTOPMOST values on 64-bit.
_user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND,   # hWnd
    ctypes.wintypes.HWND,   # hWndInsertAfter
    ctypes.c_int,           # X
    ctypes.c_int,           # Y
    ctypes.c_int,           # cx
    ctypes.c_int,           # cy
    ctypes.wintypes.UINT,   # uFlags
]
_user32.SetWindowPos.restype = ctypes.wintypes.BOOL


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
SW_MINIMIZE = 6


def show_window(hwnd: int) -> None:
    """Show a window (WinShow equivalent)."""
    _user32.ShowWindow(hwnd, SW_SHOW)


def hide_window(hwnd: int) -> None:
    """Hide a window (WinHide equivalent)."""
    _user32.ShowWindow(hwnd, SW_HIDE)


def minimize_window(hwnd: int) -> None:
    """Minimize a window to the taskbar."""
    _user32.ShowWindow(hwnd, SW_MINIMIZE)


def send_key_to_window(hwnd: int, key: str) -> None:
    """Send a single character keystroke to a window via PostMessage."""
    WM_CHAR = 0x0102
    for ch in key:
        _user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)


WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105


def send_alt_key_to_window(hwnd: int, vk: int) -> None:
    """Send an Alt+key combo to a window via PostMessage (WM_SYSKEYDOWN/UP)."""
    _user32.PostMessageW(hwnd, WM_SYSKEYDOWN, vk, 0)
    _user32.PostMessageW(hwnd, WM_SYSKEYUP, vk, 0)


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) for a window."""
    rect = ctypes.wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


# --- SendInput structures for keyboard simulation ---

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_O = 0x4F


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


def send_ctrl_o() -> None:
    """Send Ctrl+O via SendInput (requires target window to be foreground)."""
    inputs = (INPUT * 4)()
    for i, (vk, flags) in enumerate([
        (VK_CONTROL, 0),
        (VK_O, 0),
        (VK_O, KEYEVENTF_KEYUP),
        (VK_CONTROL, KEYEVENTF_KEYUP),
    ]):
        inputs[i].type = INPUT_KEYBOARD
        inputs[i].union.ki.wVk = vk
        inputs[i].union.ki.dwFlags = flags
    _user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))


def find_dialog_by_pid(pid: int, timeout_s: float = 1.0) -> int:
    """Find a dialog window (class #32770) belonging to *pid*. Returns 0 on timeout."""
    deadline = time.monotonic() + timeout_s
    class_buf = ctypes.create_unicode_buffer(256)

    while time.monotonic() < deadline:
        found: int = 0

        def callback(hwnd: int, _lparam: int) -> bool:
            nonlocal found
            if not _user32.IsWindowVisible(hwnd):
                return True
            window_pid = ctypes.wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value != pid:
                return True
            _user32.GetClassNameW(hwnd, class_buf, 256)
            if class_buf.value == "#32770":
                found = hwnd
                return False
            return True

        _user32.EnumWindows(WNDENUMPROC(callback), 0)
        if found:
            return found
        time.sleep(0.1)
    return 0


def wait_for_window_close(hwnd: int, timeout_s: float = 300.0) -> None:
    """Block until *hwnd* is destroyed or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _user32.IsWindow(hwnd):
            return
        time.sleep(0.1)
