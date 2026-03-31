"""Win32 window operations for the Python orchestrator.

Wraps ctypes calls for window manipulation that the startup sequencer
needs: find/wait for windows by PID, move, set topmost, activate, query size.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

_user32 = ctypes.windll.user32  # type: ignore[attr-defined]
_ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
_shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]

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


WM_CLOSE = 0x0010


def close_window(hwnd: int) -> None:
    """Close a window gracefully by posting WM_CLOSE."""
    if not hwnd:
        return
    _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


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


def get_captioned_window_chrome_height() -> int:
    """Return vertical non-client height for a captioned, non-resizable window.

    Queries SM_CYCAPTION (title bar) and SM_CYFIXEDFRAME (dialog border) so
    the layout engine can account for Tkinter window decorations.
    """
    SM_CYCAPTION = 4
    SM_CYFIXEDFRAME = 8
    caption = _user32.GetSystemMetrics(SM_CYCAPTION)
    frame = _user32.GetSystemMetrics(SM_CYFIXEDFRAME)
    return caption + 2 * frame


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


# --- File Open Dialog (COM IFileOpenDialog) ---

import uuid

COINIT_APARTMENTTHREADED = 0x2
CLSCTX_ALL = 0x17
SIGDN_FILESYSPATH = 0x80058000


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _make_guid(s: str) -> GUID:
    u = uuid.UUID(s)
    return GUID(u.time_low, u.time_mid, u.time_hi_version,
                (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class COMDLG_FILTERSPEC(ctypes.Structure):
    _fields_ = [
        ("pszName", ctypes.wintypes.LPCWSTR),
        ("pszSpec", ctypes.wintypes.LPCWSTR),
    ]


CLSID_FileOpenDialog = _make_guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")
IID_IFileOpenDialog = _make_guid("d57c7288-d4ad-4768-be02-9d969532d960")
IID_IShellItem = _make_guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")

# IFileOpenDialog vtable indices (IUnknown + IModalWindow + IFileDialog + IFileOpenDialog)
_VTBL_RELEASE = 2
_VTBL_SHOW = 3
_VTBL_SET_FILE_TYPES = 4
_VTBL_SET_FOLDER = 12
_VTBL_GET_RESULT = 20
# IShellItem vtable indices
_VTBL_GET_DISPLAY_NAME = 5


def _vtbl_call(obj_addr: int, index: int, restype: type, *argtypes: type):
    """Build a callable for COM vtable method at *index*. Caller passes 'this' as first arg."""
    vtbl = ctypes.c_void_p.from_address(obj_addr).value
    func_ptr = ctypes.c_void_p.from_address(
        vtbl + index * ctypes.sizeof(ctypes.c_void_p)
    ).value
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(func_ptr)


def _release(obj_addr: int) -> None:
    _vtbl_call(obj_addr, _VTBL_RELEASE, ctypes.c_ulong)(obj_addr)


def show_open_file_dialog(initial_dir: str, owner_hwnd: int = 0) -> str | None:
    """Show a native Windows file-open dialog starting at *initial_dir*.

    Uses COM IFileOpenDialog with SetFolder (not SetDefaultFolder) so the
    dialog always opens at the specified directory, ignoring Windows' MRU
    cache.  The owner_hwnd parameter positions the dialog near that window.

    Returns the selected file path, or None if the user cancelled.
    """
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        return _show_ifile_dialog(initial_dir, owner_hwnd)
    finally:
        _ole32.CoUninitialize()


def _show_ifile_dialog(initial_dir: str, owner_hwnd: int) -> str | None:
    dialog = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(
        ctypes.byref(CLSID_FileOpenDialog), None, CLSCTX_ALL,
        ctypes.byref(IID_IFileOpenDialog), ctypes.byref(dialog),
    )
    if hr != 0:
        return None
    try:
        # Set initial folder (always forces, ignores MRU)
        if initial_dir:
            folder = ctypes.c_void_p()
            hr = _shell32.SHCreateItemFromParsingName(
                initial_dir, None, ctypes.byref(IID_IShellItem), ctypes.byref(folder),
            )
            if hr == 0:
                _vtbl_call(dialog.value, _VTBL_SET_FOLDER,
                           ctypes.HRESULT, ctypes.c_void_p)(dialog.value, folder.value)
                _release(folder.value)

        # Set video file filters
        specs = (COMDLG_FILTERSPEC * 2)(
            COMDLG_FILTERSPEC("Video Files",
                              "*.mp4;*.mkv;*.avi;*.wmv;*.mov;*.flv;*.webm;*.m4v;*.ts;*.mpg;*.mpeg"),
            COMDLG_FILTERSPEC("All Files", "*.*"),
        )
        _vtbl_call(dialog.value, _VTBL_SET_FILE_TYPES,
                   ctypes.HRESULT, ctypes.c_uint,
                   ctypes.POINTER(COMDLG_FILTERSPEC))(dialog.value, 2, specs)

        # Show (blocks until user selects or cancels)
        hr = _vtbl_call(dialog.value, _VTBL_SHOW,
                        ctypes.HRESULT, ctypes.wintypes.HWND)(dialog.value, owner_hwnd)
        if hr != 0:
            return None

        # Get selected file path
        result = ctypes.c_void_p()
        hr = _vtbl_call(dialog.value, _VTBL_GET_RESULT,
                        ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            dialog.value, ctypes.byref(result))
        if hr != 0:
            return None
        try:
            path_ptr = ctypes.wintypes.LPWSTR()
            hr = _vtbl_call(result.value, _VTBL_GET_DISPLAY_NAME,
                            ctypes.HRESULT, ctypes.c_int,
                            ctypes.POINTER(ctypes.wintypes.LPWSTR))(
                result.value, SIGDN_FILESYSPATH, ctypes.byref(path_ptr))
            if hr != 0:
                return None
            path = path_ptr.value
            _ole32.CoTaskMemFree(path_ptr)
            return path
        finally:
            _release(result.value)
    finally:
        _release(dialog.value)
