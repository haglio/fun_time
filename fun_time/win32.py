"""Win32 window and process operations for the Python orchestrator.

Wraps ctypes calls for window manipulation that the startup sequencer
needs (find/wait for windows by PID, move, set topmost, activate, query
size) and for process queries (liveness, executable image name).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

_user32 = ctypes.windll.user32  # type: ignore[attr-defined]
_ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
_shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
_kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
_dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]

# AppUserModelID — must match the value set on the pinned taskbar shortcut.
APP_USER_MODEL_ID = "FunTime.App"

# Constants
SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = ctypes.wintypes.HWND(-1)
HWND_NOTOPMOST = ctypes.wintypes.HWND(-2)
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

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

# Process-query bindings. HANDLE argtypes/restype matter on 64-bit for the
# same reason as the HWND declarations above.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_kernel32.OpenProcess.argtypes = [
    ctypes.wintypes.DWORD,  # dwDesiredAccess
    ctypes.wintypes.BOOL,   # bInheritHandle
    ctypes.wintypes.DWORD,  # dwProcessId
]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.wintypes.HANDLE,                  # hProcess
    ctypes.wintypes.DWORD,                   # dwFlags
    ctypes.wintypes.LPWSTR,                  # lpExeName
    ctypes.POINTER(ctypes.wintypes.DWORD),   # lpdwSize (in/out)
]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
_kernel32.GetExitCodeProcess.argtypes = [
    ctypes.wintypes.HANDLE,                  # hProcess
    ctypes.POINTER(ctypes.wintypes.DWORD),   # lpExitCode
]
_kernel32.GetExitCodeProcess.restype = ctypes.wintypes.BOOL
_kernel32.GetProcessTimes.argtypes = [
    ctypes.wintypes.HANDLE,                     # hProcess
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpCreationTime
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpExitTime
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpKernelTime
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpUserTime
]
_kernel32.GetProcessTimes.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

# GetExitCodeProcess reports this while the process is still running.
_STILL_ACTIVE = 259


WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.BOOL,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)


WM_CLOSE = 0x0010


def set_app_user_model_id(app_id: str) -> None:
    """Set the AppUserModelID for the current process.

    This must be called before any windows are created so the taskbar can
    group the process's windows with the matching pinned shortcut.
    """
    hr = _shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    if hr < 0:  # FAILED() macro
        raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed: HRESULT 0x{hr:08x}")


def get_process_image_name(pid: int) -> str | None:
    """Return the full executable path of the process *pid*.

    Returns None when the process no longer exists (or cannot be opened),
    which callers use to detect that a recorded PID is stale.
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        _kernel32.CloseHandle(handle)


def get_process_creation_time(pid: int) -> int | None:
    """Return the FILETIME at which the process now holding *pid* was created.

    Windows hands a freed PID back out within seconds, so a PID alone does not
    name a process.  ``(pid, creation_time)`` does: a process can only take a
    PID after its previous owner is gone, so the newcomer's creation time is
    strictly later.  Record this alongside a PID and compare it before killing,
    and a recycled PID is recognised rather than shot.

    ``GetProcessTimes`` fills lpCreationTime with a FILETIME (100-nanosecond
    ticks since 1601-01-01 UTC) and accepts a handle opened for
    PROCESS_QUERY_LIMITED_INFORMATION.  Returns None when the process no longer
    exists (or cannot be opened).
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = ctypes.wintypes.FILETIME()
        unused = (ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME())
        if not _kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), *(ctypes.byref(t) for t in unused)
        ):
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def is_process_alive(pid: int) -> bool:
    """Check whether *pid* refers to a currently running process.

    os.kill(pid, 0) raises WinError 87 for valid PIDs on Python 3.14 /
    Windows 11, and OpenProcess alone still succeeds for exited processes
    whose kernel object is kept alive by an open handle, so liveness comes
    from GetExitCodeProcess reporting STILL_ACTIVE.
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def close_window(hwnd: int) -> None:
    """Close a window gracefully by posting WM_CLOSE."""
    if not hwnd:
        return
    _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def find_window_by_pid(pid: int, *, include_hidden: bool = False) -> int:
    """Find a top-level window belonging to *pid*. Returns 0 if not found.

    Matches AHK's ``DetectHiddenWindows False`` behavior: only considers
    windows that are visible (``IsWindowVisible``) and have a non-empty title.
    This avoids grabbing internal surfaces like Direct3D rendering windows.

    Set *include_hidden* to also match windows with WS_VISIBLE cleared — needed
    for the dashboard, which is hidden (SW_HIDE) behind the loading overlay when
    the startup sequencer resolves its handle.  The non-empty-title filter still
    applies, so this does not match untitled internal surfaces.
    """
    best: int = 0

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal best
        window_pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid:
            return True
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
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


def wait_for_window_by_title(
    title: str, timeout_s: float = 5.0, *, exact: bool = False, include_hidden: bool = False
) -> int:
    """Poll for a visible window whose title contains (or equals) *title*.

    Useful when the PID-based lookup fails (e.g. venv launcher PID differs
    from the interpreter PID that owns the window).  *include_hidden* is
    forwarded to :func:`find_window_by_title` for SW_HIDE'd windows.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hwnd = find_window_by_title(title, exact=exact, include_hidden=include_hidden)
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



def is_window_topmost(hwnd: int) -> bool:
    """Check whether a window has the WS_EX_TOPMOST extended style."""
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    return bool(ex_style & WS_EX_TOPMOST)


def activate_window(hwnd: int) -> None:
    """Bring a window to the foreground."""
    _user32.SetForegroundWindow(hwnd)


def find_window_by_title(title: str, *, exact: bool = False, include_hidden: bool = False) -> int:
    """Find a visible window whose title contains (or, with *exact*, equals)
    *title*. Returns 0 if not found.

    Use exact=True when the title is a substring of another managed window's
    title (e.g. "Nau" is contained in "Genau").  Set *include_hidden* to also
    match windows with WS_VISIBLE cleared (SW_HIDE) — needed to resolve the
    dashboard while it is hidden behind the loading overlay, whose window PID
    differs from the launcher PID so only the title lookup can find it.
    """
    best: int = 0
    buf = ctypes.create_unicode_buffer(256)

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal best
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
            return True
        _user32.GetWindowTextW(hwnd, buf, 256)
        matched = buf.value == title if exact else title in buf.value
        if matched:
            best = hwnd
            return False
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return best


SW_SHOW = 5
SW_HIDE = 0
SW_MINIMIZE = 6
SW_SHOWMINNOACTIVE = 7


def show_window(hwnd: int) -> None:
    """Show a window (WinShow equivalent)."""
    _user32.ShowWindow(hwnd, SW_SHOW)


def hide_window(hwnd: int) -> None:
    """Hide a window (WinHide equivalent)."""
    _user32.ShowWindow(hwnd, SW_HIDE)


def minimize_window(hwnd: int, *, activate: bool = True) -> None:
    """Minimize a window to the taskbar.

    When *activate* is False, uses SW_SHOWMINNOACTIVE to minimize
    without activating the next window in z-order — prevents focus
    stealing when minimizing multiple windows in sequence.
    """
    _user32.ShowWindow(hwnd, SW_MINIMIZE if activate else SW_SHOWMINNOACTIVE)


def restore_window(hwnd: int, *, activate: bool = True) -> None:
    """Restore (un-minimize) a window to its previous size and position.

    When *activate* is False, uses SW_SHOWNOACTIVATE so restoring several
    windows in sequence never yanks focus from one to the next.
    """
    _user32.ShowWindow(hwnd, SW_RESTORE if activate else SW_SHOWNOACTIVATE)


def disable_window_transitions(hwnd: int) -> None:
    """Force-disable this window's DWM open/minimize/restore animations.

    The primary-slot players (Nau, Genau) are swapped by minimizing the idle
    one and restoring the active one, so both keep a taskbar button the whole
    session (no reappearing-icon flash).  DWMWA_TRANSITIONS_FORCEDISABLED makes
    that minimize/restore instantaneous — no fly-to-taskbar animation to see.
    """
    DWMWA_TRANSITIONS_FORCEDISABLED = 3
    value = ctypes.wintypes.BOOL(1)  # TRUE
    _dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_TRANSITIONS_FORCEDISABLED, ctypes.byref(value), ctypes.sizeof(value)
    )


def is_window_minimized(hwnd: int) -> bool:
    """True if the window is currently minimized (iconic)."""
    return bool(_user32.IsIconic(hwnd))


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


# --- Shortcut AppUserModelID (COM IShellLink + IPersistFile + IPropertyStore) ---

CLSID_ShellLink = _make_guid("00021401-0000-0000-C000-000000000046")
IID_IShellLinkW = _make_guid("000214F9-0000-0000-C000-000000000046")
IID_IPersistFile = _make_guid("0000010B-0000-0000-C000-000000000046")
IID_IPropertyStore = _make_guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


PKEY_AppUserModel_ID = PROPERTYKEY(
    _make_guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5
)

VT_LPWSTR = 31


class PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("pwszVal", ctypes.wintypes.LPWSTR),
        ("_pad", ctypes.c_void_p),
    ]


STGM_READWRITE = 0x00000002

# IPersistFile vtable indices (IUnknown=0..2 + IPersist::GetClassID=3)
_VTBL_IPF_LOAD = 5
_VTBL_IPF_SAVE = 6

# IPropertyStore vtable indices (IUnknown=0..2)
_VTBL_IPS_GET_VALUE = 5
_VTBL_IPS_SET_VALUE = 6
_VTBL_IPS_COMMIT = 7

# IUnknown
_VTBL_QI = 0


def _query_interface(obj_addr: int, iid: GUID) -> int:
    """QueryInterface on a COM object. Returns the new interface pointer or raises."""
    out = ctypes.c_void_p()
    hr = _vtbl_call(obj_addr, _VTBL_QI, ctypes.HRESULT,
                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        obj_addr, ctypes.byref(iid), ctypes.byref(out))
    if hr < 0:  # FAILED() macro
        raise OSError(f"QueryInterface failed: HRESULT 0x{hr:08x}")
    return out.value


def set_shortcut_app_user_model_id(lnk_path: str, app_id: str) -> None:
    """Set the AppUserModelID property on a .lnk shortcut file.

    Uses COM (IShellLink → IPersistFile → IPropertyStore) to write the
    System.AppUserModel.ID property, which Windows uses to match a running
    process's windows with a pinned taskbar shortcut.
    """
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        _set_lnk_aumid(lnk_path, app_id)
    finally:
        _ole32.CoUninitialize()


def _set_lnk_aumid(lnk_path: str, app_id: str) -> None:
    # Create IShellLink instance
    shell_link = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(
        ctypes.byref(CLSID_ShellLink), None, CLSCTX_ALL,
        ctypes.byref(IID_IShellLinkW), ctypes.byref(shell_link),
    )
    if hr < 0:
        raise OSError(f"CoCreateInstance(ShellLink) failed: HRESULT 0x{hr:08x}")
    try:
        # Get IPersistFile and load the .lnk
        persist_file = _query_interface(shell_link.value, IID_IPersistFile)
        try:
            hr = _vtbl_call(persist_file, _VTBL_IPF_LOAD,
                            ctypes.HRESULT, ctypes.wintypes.LPCWSTR, ctypes.c_ulong)(
                persist_file, lnk_path, STGM_READWRITE)
            if hr < 0:
                raise OSError(f"IPersistFile::Load failed: HRESULT 0x{hr:08x}")

            # Get IPropertyStore and set the AUMID
            prop_store = _query_interface(shell_link.value, IID_IPropertyStore)
            try:
                pv = PROPVARIANT()
                pv.vt = VT_LPWSTR
                pv.pwszVal = app_id

                hr = _vtbl_call(prop_store, _VTBL_IPS_SET_VALUE,
                                ctypes.HRESULT,
                                ctypes.POINTER(PROPERTYKEY),
                                ctypes.POINTER(PROPVARIANT))(
                    prop_store,
                    ctypes.byref(PKEY_AppUserModel_ID),
                    ctypes.byref(pv))
                if hr < 0:  # FAILED() macro — S_FALSE (1) is success
                    raise OSError(f"IPropertyStore::SetValue failed: HRESULT 0x{hr:08x}")

                hr = _vtbl_call(prop_store, _VTBL_IPS_COMMIT, ctypes.HRESULT)(prop_store)
                if hr < 0:
                    raise OSError(f"IPropertyStore::Commit failed: HRESULT 0x{hr:08x}")
            finally:
                _release(prop_store)

            # Save the .lnk back to disk
            hr = _vtbl_call(persist_file, _VTBL_IPF_SAVE,
                            ctypes.HRESULT, ctypes.wintypes.LPCWSTR, ctypes.wintypes.BOOL)(
                persist_file, lnk_path, True)
            if hr < 0:
                raise OSError(f"IPersistFile::Save failed: HRESULT 0x{hr:08x}")
        finally:
            _release(persist_file)
    finally:
        _release(shell_link.value)


def _read_shortcut_app_user_model_id(lnk_path: str) -> str | None:
    """Read the AppUserModelID property from a .lnk file (for testing)."""
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        return _get_lnk_aumid(lnk_path)
    finally:
        _ole32.CoUninitialize()


def _get_lnk_aumid(lnk_path: str) -> str | None:
    shell_link = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(
        ctypes.byref(CLSID_ShellLink), None, CLSCTX_ALL,
        ctypes.byref(IID_IShellLinkW), ctypes.byref(shell_link),
    )
    if hr != 0:
        return None
    try:
        persist_file = _query_interface(shell_link.value, IID_IPersistFile)
        try:
            hr = _vtbl_call(persist_file, _VTBL_IPF_LOAD,
                            ctypes.HRESULT, ctypes.wintypes.LPCWSTR, ctypes.c_ulong)(
                persist_file, lnk_path, 0)  # STGM_READ = 0
            if hr != 0:
                return None

            prop_store = _query_interface(shell_link.value, IID_IPropertyStore)
            try:
                pv = PROPVARIANT()
                hr = _vtbl_call(prop_store, _VTBL_IPS_GET_VALUE,
                                ctypes.HRESULT,
                                ctypes.POINTER(PROPERTYKEY),
                                ctypes.POINTER(PROPVARIANT))(
                    prop_store,
                    ctypes.byref(PKEY_AppUserModel_ID),
                    ctypes.byref(pv))
                if hr != 0 or pv.vt != VT_LPWSTR:
                    return None
                return pv.pwszVal
            finally:
                _release(prop_store)
        finally:
            _release(persist_file)
    finally:
        _release(shell_link.value)


# --- File Open Dialog (COM IFileOpenDialog) ---

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
