"""Win32 window operations for the Python orchestrator.

Wraps ctypes calls for window manipulation that the startup sequencer
needs: find/wait for windows by PID, move, set topmost, activate, query size.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

_user32 = ctypes.windll.user32  # type: ignore[attr-defined]
_comdlg32 = ctypes.windll.comdlg32  # type: ignore[attr-defined]
_ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]

# Constants
SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = ctypes.wintypes.HWND(-1)
HWND_NOTOPMOST = ctypes.wintypes.HWND(-2)
LSFW_LOCK = 1
LSFW_UNLOCK = 2

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


def move_window(hwnd: int, x: int, y: int, w: int, h: int, *, activate: bool = True) -> None:
    """Restore and reposition a window (WinRestore + WinMove equivalent).

    When *activate* is False the window is shown without stealing focus
    (uses SW_SHOWNOACTIVATE instead of SW_RESTORE).
    """
    _user32.ShowWindow(hwnd, SW_RESTORE if activate else SW_SHOWNOACTIVATE)
    _user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


def set_always_on_top(hwnd: int, on_top: bool) -> None:
    """Set or clear the always-on-top flag for a window."""
    insert_after = HWND_TOPMOST if on_top else HWND_NOTOPMOST
    _user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def activate_window(hwnd: int) -> None:
    """Bring a window to the foreground."""
    _user32.SetForegroundWindow(hwnd)


def get_foreground_window() -> int:
    """Return the HWND of the current foreground window (0 if none)."""
    return _user32.GetForegroundWindow() or 0


def lock_set_foreground_window() -> None:
    """Prevent other processes from stealing foreground via SetForegroundWindow.

    Calls from other processes will flash the taskbar button instead of
    actually activating their window.  Call unlock_set_foreground_window()
    to release.
    """
    _user32.LockSetForegroundWindow(LSFW_LOCK)


def unlock_set_foreground_window() -> None:
    """Re-allow SetForegroundWindow calls from other processes."""
    _user32.LockSetForegroundWindow(LSFW_UNLOCK)


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
    """Send a single keystroke to a window via PostMessage (WM_KEYDOWN/UP).

    Uses WM_KEYDOWN + WM_KEYUP rather than WM_CHAR so that applications
    which only process key-down events (e.g. VLC media shortcuts) respond.
    """
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    for ch in key:
        vk = ord(ch.upper())
        _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
        _user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)


def send_vk_to_window(hwnd: int, vk: int) -> None:
    """Send a virtual-key code to a window via PostMessage (WM_KEYDOWN/UP)."""
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    _user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) for a window."""
    rect = ctypes.wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


# --- File Open Dialog (GetOpenFileNameW) ---

OFN_FILEMUSTEXIST = 0x00001000
OFN_PATHMUSTEXIST = 0x00000800
OFN_NOCHANGEDIR = 0x00000008
COINIT_APARTMENTTHREADED = 0x2

_VIDEO_FILTER = (
    "Video Files\0"
    "*.mp4;*.mkv;*.avi;*.wmv;*.mov;*.flv;*.webm;*.m4v;*.ts;*.mpg;*.mpeg\0"
    "All Files\0*.*\0"
)


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", ctypes.wintypes.DWORD),
        ("hwndOwner", ctypes.wintypes.HWND),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("lpstrFilter", ctypes.wintypes.LPCWSTR),
        ("lpstrCustomFilter", ctypes.wintypes.LPWSTR),
        ("nMaxCustFilter", ctypes.wintypes.DWORD),
        ("nFilterIndex", ctypes.wintypes.DWORD),
        ("lpstrFile", ctypes.wintypes.LPWSTR),
        ("nMaxFile", ctypes.wintypes.DWORD),
        ("lpstrFileTitle", ctypes.wintypes.LPWSTR),
        ("nMaxFileTitle", ctypes.wintypes.DWORD),
        ("lpstrInitialDir", ctypes.wintypes.LPCWSTR),
        ("lpstrTitle", ctypes.wintypes.LPCWSTR),
        ("Flags", ctypes.wintypes.DWORD),
        ("nFileOffset", ctypes.wintypes.WORD),
        ("nFileExtension", ctypes.wintypes.WORD),
        ("lpstrDefExt", ctypes.wintypes.LPCWSTR),
        ("lCustData", ctypes.c_void_p),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", ctypes.wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", ctypes.wintypes.DWORD),
        ("FlagsEx", ctypes.wintypes.DWORD),
    ]


def show_open_file_dialog(initial_dir: str, owner_hwnd: int = 0) -> str | None:
    """Show a native Windows file-open dialog starting at *initial_dir*.

    Uses GetOpenFileNameW which, on Vista+, renders as a modern IFileDialog
    with lpstrInitialDir mapped to IFileDialog::SetFolder — so the dialog
    opens at the correct directory from the first frame (no address-bar jank).

    Returns the selected file path, or None if the user cancelled.
    """
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        buf = ctypes.create_unicode_buffer(1024)
        ofn = OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
        ofn.hwndOwner = owner_hwnd
        ofn.lpstrFilter = _VIDEO_FILTER
        ofn.nFilterIndex = 1
        ofn.lpstrFile = ctypes.cast(buf, ctypes.wintypes.LPWSTR)
        ofn.nMaxFile = 1024
        ofn.lpstrInitialDir = initial_dir
        ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR

        if _comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return buf.value or None
        return None
    finally:
        _ole32.CoUninitialize()
