"""Win32 window and process operations for the Python orchestrator.

Wraps ctypes calls for window manipulation that the startup sequencer
needs (find/wait for windows by PID, move, set topmost, activate, query
size) and for process queries (liveness, executable image name).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from dataclasses import dataclass

from fun_time.win32_loader import load_dll, win_functype

logger = logging.getLogger(__name__)

_user32 = load_dll("user32")
_ole32 = load_dll("ole32")
_shell32 = load_dll("shell32")
_kernel32 = load_dll("kernel32")
_dwmapi = load_dll("dwmapi")

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
GW_HWNDNEXT = 2  # next window DOWN the z-order (GetWindow relationship)

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


WNDENUMPROC = win_functype(
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
    and a recycled PID is recognized rather than shot.

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


def list_child_pids(parent_pid: int) -> list[int]:
    """The pids whose recorded parent is *parent_pid*, via a Toolhelp snapshot.

    A recorded child pid is not always the pid that owns the windows: a venv's
    ``Scripts`` launcher spawns the real interpreter as a child and keeps the
    recorded pid for itself.  This is the one hop that recovers the family.
    """
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD),
            ("cntUsage", ctypes.wintypes.DWORD),
            ("th32ProcessID", ctypes.wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.wintypes.ULONG)),
            ("th32ModuleID", ctypes.wintypes.DWORD),
            ("cntThreads", ctypes.wintypes.DWORD),
            ("th32ParentProcessID", ctypes.wintypes.DWORD),
            ("pcPriClassBase", ctypes.wintypes.LONG),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    children: list[int] = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.th32ParentProcessID == parent_pid:
                    children.append(int(entry.th32ProcessID))
                if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        _kernel32.CloseHandle(snapshot)
    return children


def find_window_for_process(pid: int, title: str) -> int:
    """*pid*'s — or its direct children's — window titled exactly *title*, or 0.

    Pid AND title, because a process can own several titled windows (the
    hosted Origenerator: a main window plus a show per satellite region) and a
    title alone can land on another process's window (a standalone
    Origenerator carries the same captions).  The children matter because a
    recorded pid can be a launcher's: a venv's ``Scripts\\python.exe`` spawns
    the interpreter that actually owns the windows as a child and exits the
    lookup empty-handed.  One generation is the launcher pattern; nothing
    spawns windows two shims deep.  Includes hidden/minimized windows: the
    hosted app's main window boots parked and must still resolve.
    """
    if not pid:
        return 0
    pids = {pid, *list_child_pids(pid)}
    best: int = 0

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal best
        window_pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value not in pids:
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value != title:
            return True
        best = hwnd
        return False  # stop enumeration

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return best


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


# How long one of those calls is given before the session stops waiting on that
# window.  A window whose owner is pumping answers in microseconds, so this is
# only ever spent on one that has stopped — and it is spent once per call, so a
# whole startup pass over the session's windows cannot cost more than a few
# seconds.
HUNG_WINDOW_TIMEOUT_S = 1.5


def _owned_by_this_process(hwnd: int) -> bool:
    """Whether *hwnd* belongs to the process making the call."""
    pid = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == _kernel32.GetCurrentProcessId()


def _without_hanging(call, hwnd, *args, what: str) -> bool:
    """Make a cross-process window call, and give up on a window that has stopped
    answering.  True if the call returned, False if that window is hung.

    ``SetWindowPos`` and ``ShowWindow`` do not merely set state: each SENDS
    messages to the thread that owns the window (WM_WINDOWPOSCHANGING /
    WM_WINDOWPOSCHANGED, WM_SHOWWINDOW) and waits for that thread to handle them.
    Across processes — which every window here is — a player whose own loop has
    stalled therefore blocks the caller *forever*: the send has no timeout, and
    no flag on our side changes that.

    One did, and it took the whole session with it: Genau's main thread stopped
    inside a file write, startup's topmost pass called this on Genau's window and
    never came back, so the main slot was never revealed, the hotkey script was
    never launched, and Ctrl+Alt+Q could not quit a session with no way left to
    close its players.  Nothing said which window, either.

    So the call is made on a throwaway thread and waited on for
    HUNG_WINDOW_TIMEOUT_S.  A healthy window answers in microseconds and nothing
    changes — including the ORDER the caller makes these calls in, which is what
    stacks Genau's HUD above Nau's video and which posting the requests
    (SWP_ASYNCWINDOWPOS) would have given up.  A window that does not answer is
    named in the log and left where it is, and the session carries on without it.
    The thread stays blocked in the kernel until that window's owner recovers or
    dies; that is one leaked thread per call to a hung window, and the cost of
    not leaking it is the wedge above.

    Our OWN windows are called straight, and must be: the send would go to this
    process's UI thread, which is the very thread waiting here — so the worker
    would wait for a pump that cannot happen until the wait returns, and the
    dashboard would spend HUNG_WINDOW_TIMEOUT_S failing to band its own reference
    popup.  A window this process owns cannot be hung from our side anyway: if
    its loop has stalled, we are the ones who stalled it.
    """
    if _owned_by_this_process(hwnd):
        call(hwnd, *args)
        return True

    done = threading.Event()

    def run() -> None:
        try:
            call(hwnd, *args)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name=f"win32-{what}").start()
    if done.wait(HUNG_WINDOW_TIMEOUT_S):
        return True
    logger.warning(
        "%s did not return in %.1fs — that window has stopped answering; "
        "carrying on without it", what, HUNG_WINDOW_TIMEOUT_S,
    )
    return False


def move_window(hwnd: int, x: int, y: int, w: int, h: int, *, activate: bool = True) -> None:
    """Restore and reposition a window (WinRestore + WinMove equivalent).

    When *activate* is False the window is shown without stealing focus
    (uses SW_SHOWNOACTIVATE instead of SW_RESTORE).
    """
    _without_hanging(
        _user32.ShowWindow, hwnd, SW_RESTORE if activate else SW_SHOWNOACTIVATE,
        what=f"ShowWindow({hwnd})",
    )
    _without_hanging(
        _user32.SetWindowPos, hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE,
        what=f"SetWindowPos({hwnd})",
    )


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Where *hwnd* sits, as (x, y, width, height), or None if it is gone.

    Read so a second window can be stood exactly on a first — the library
    browser fills the main player's rect, since that is where the video it
    picks will play.
    """
    rect = ctypes.wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def set_always_on_top(hwnd: int, on_top: bool) -> None:
    """Set or clear the always-on-top flag for a window.

    Through :func:`_without_hanging`: this is the call a stalled player froze the
    whole session on, because it waits for that player's own thread.
    """
    insert_after = HWND_TOPMOST if on_top else HWND_NOTOPMOST
    _without_hanging(
        _user32.SetWindowPos, hwnd, insert_after, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        what=f"set_always_on_top({hwnd}, {on_top})",
    )


def is_window_topmost(hwnd: int) -> bool:
    """Check whether a window has the WS_EX_TOPMOST extended style."""
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    return bool(ex_style & WS_EX_TOPMOST)


@dataclass(frozen=True)
class StackedWindow:
    """A visible top-level window, as seen while walking the z-order."""

    hwnd: int
    title: str
    topmost: bool
    rect: tuple[int, int, int, int]  # x, y, width, height (screen coords)


# Framed windows carry an INVISIBLE resize border (~7-8px per edge on Windows
# 10/11) that GetWindowRect includes: a maximized Chrome reports itself 8px
# onto the neighboring monitor, and a docked dashboard reports 8px into the
# player beside it.  An intersection this thin is that ghost frame, not
# anything the eye can see covered, so the overlap test ignores it.
_FRAME_GHOST_PX = 12


def _rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """Whether two (x, y, w, h) rectangles share VISIBLE interior area.

    A shared edge (touching but not crossing) is not overlap — the portrait
    satellite's bottom edge meets Nau's top edge, and that abutment must not
    read as coverage.  Nor is an intersection thinner than a window's
    invisible resize frame (see ``_FRAME_GHOST_PX``): those slivers had the
    startup log warning that a maximized Chrome on one monitor "covered" the
    player on the next monitor over.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap_w = min(ax + aw, bx + bw) - max(ax, bx)
    overlap_h = min(ay + ah, by + bh) - max(ay, by)
    return overlap_w > _FRAME_GHOST_PX and overlap_h > _FRAME_GHOST_PX


def windows_obscuring(
    target_hwnd: int, stack: list[StackedWindow]
) -> list[StackedWindow]:
    """Windows in *stack* (ordered front-to-back) that cover *target_hwnd*.

    A window covers the target when it sits ABOVE it in the z-order and its
    rect overlaps the target's.  Returns [] when the target is frontmost over
    its own rect, or is absent from the stack.

    This is what ``is_window_topmost`` cannot tell you: a window may carry the
    topmost flag yet still be buried under another overlapping window that was
    promoted after it.  Only the real stacking order answers "is Nau visible."
    """
    idx = next((i for i, w in enumerate(stack) if w.hwnd == target_hwnd), None)
    if idx is None:
        return []
    target_rect = stack[idx].rect
    return [w for w in stack[:idx] if _rects_overlap(w.rect, target_rect)]


def iter_zorder() -> list[StackedWindow]:
    """Every visible, titled, non-minimized top-level window, front-to-back.

    Walks ``GetTopWindow`` + ``GW_HWNDNEXT`` — the real stacking order — rather
    than ``EnumWindows`` (whose order is unspecified).  Untitled and minimized
    windows are skipped: they never visibly cover another window, and the title
    filter drops the sea of internal/system surfaces so the result stays legible
    in a log line.
    """
    out: list[StackedWindow] = []
    hwnd = _user32.GetTopWindow(0)
    while hwnd:
        if _user32.IsWindowVisible(hwnd) and not _user32.IsIconic(hwnd):
            length = _user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buf, length + 1)
                rect = ctypes.wintypes.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(rect))
                out.append(
                    StackedWindow(
                        hwnd=hwnd,
                        title=buf.value,
                        topmost=is_window_topmost(hwnd),
                        rect=(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
                    )
                )
        hwnd = _user32.GetWindow(hwnd, GW_HWNDNEXT)
    return out


def activate_window(hwnd: int) -> None:
    """Bring a window to the foreground."""
    _user32.SetForegroundWindow(hwnd)


# HWND/DWORD argtypes for the foreground helpers below, declared for the same
# 64-bit truncation reason as SetWindowPos above.  AttachThreadInput takes
# thread ids, not handles, so its two DWORDs are the whole signature.
_user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
_user32.IsWindow.restype = ctypes.wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
_user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
_user32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
_user32.BringWindowToTop.restype = ctypes.wintypes.BOOL
_user32.AttachThreadInput.argtypes = [
    ctypes.wintypes.DWORD,  # idAttach
    ctypes.wintypes.DWORD,  # idAttachTo
    ctypes.wintypes.BOOL,   # fAttach
]
_user32.AttachThreadInput.restype = ctypes.wintypes.BOOL
_kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD


def window_exists(hwnd: int) -> bool:
    """Whether *hwnd* still names a live window.

    A handle captured at startup outlives the window it named — closing the
    window leaves the number behind — so anything that must reach *that* window
    and no other has to ask before it acts.
    """
    return bool(hwnd) and bool(_user32.IsWindow(hwnd))


def force_foreground_window(hwnd: int) -> bool:
    """Take the foreground for *hwnd* from a process that does not hold it.

    Windows refuses ``SetForegroundWindow`` outright unless the calling process
    owns the foreground window or received the last input event — and the bridge
    is neither when a hotkey lands: AHK got the key, and a player owns the
    screen.  The refusal is silent; it flashes the taskbar button and delivers no
    WM_ACTIVATE at all, which is exactly the message the window has to see.
    Attaching this thread's input queue to the foreground window's thread makes
    the two one queue, and a thread sharing the foreground thread's queue is one
    of the cases the rule accepts, so the call goes through.

    Returns whether the window really ended up in the foreground.  A False is
    worth logging but not worth acting on: on a non-input desktop (the hidden
    desktop the integration suite runs on) there is no foreground window to be,
    so this reads False there while the activation itself still lands.
    """
    if not window_exists(hwnd):
        return False
    foreground = _user32.GetForegroundWindow()
    this_thread = _kernel32.GetCurrentThreadId()
    other_thread = _user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(
        other_thread
        and other_thread != this_thread
        and _user32.AttachThreadInput(other_thread, this_thread, True)
    )
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(other_thread, this_thread, False)
    return int(_user32.GetForegroundWindow() or 0) == hwnd


# argtypes matter on 64-bit: without them ctypes marshals the HWND as a 32-bit
# c_int and truncates the handle, and the process-id out-param pointer must be a
# real pointer (same reasoning as the SetWindowPos/OpenProcess declarations).
_user32.GetWindowThreadProcessId.argtypes = [
    ctypes.wintypes.HWND,                   # hWnd
    ctypes.POINTER(ctypes.wintypes.DWORD),  # lpdwProcessId (out)
]
_user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD


def find_window_by_title(title: str, *, exact: bool = False, include_hidden: bool = False) -> int:
    """Find a visible window whose title contains (or, with *exact*, equals)
    *title*. Returns 0 if not found.

    Use exact=True when the title is carried at the front of another managed
    window's: the dashboard is "Fun Time", and the loading cover, the closing
    cover and the library browser are "Fun Time Loading", "Fun Time Closing"
    and "Fun Time Library", so a substring lookup for the panel answers with
    whichever of the four the enumeration reaches first.  Set *include_hidden* to also
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


SW_MINIMIZE = 6
SW_SHOWMINNOACTIVE = 7


def minimize_window(hwnd: int, *, activate: bool = True) -> None:
    """Minimize a window to the taskbar.

    When *activate* is False, uses SW_SHOWMINNOACTIVE to minimize
    without activating the next window in z-order — prevents focus
    stealing when minimizing multiple windows in sequence.

    Through :func:`_without_hanging` for the reason set_always_on_top is: a
    player that has stopped pumping would otherwise freeze whoever parked it —
    the mode switch, omniminimize, or that player's own HUD button.
    """
    _without_hanging(
        _user32.ShowWindow, hwnd, SW_MINIMIZE if activate else SW_SHOWMINNOACTIVE,
        what=f"minimize_window({hwnd})",
    )


def restore_window(hwnd: int, *, activate: bool = True) -> None:
    """Restore (un-minimize) a window to its previous size and position.

    When *activate* is False, uses SW_SHOWNOACTIVATE so restoring several
    windows in sequence never yanks focus from one to the next.

    Through :func:`_without_hanging`, so one player that has stopped answering
    cannot hold up the mode switch, the omnipause resume, or the rest of the
    windows coming back with it.
    """
    _without_hanging(
        _user32.ShowWindow, hwnd, SW_RESTORE if activate else SW_SHOWNOACTIVATE,
        what=f"restore_window({hwnd})",
    )


def disable_window_transitions(hwnd: int) -> None:
    """Force-disable this window's DWM open/minimize/restore animations.

    The main-slot players (Nau, Genau) are swapped by minimizing the idle
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


# --- COM plumbing (the shortcut's AppUserModelID) ---

import uuid

COINIT_APARTMENTTHREADED = 0x2

# IUnknown vtable index, for _release below.
_VTBL_RELEASE = 2
CLSCTX_ALL = 0x17


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


def _vtbl_call(obj_addr: int, index: int, restype: type, *argtypes: type):
    """Build a callable for COM vtable method at *index*. Caller passes 'this' as first arg."""
    vtbl = ctypes.c_void_p.from_address(obj_addr).value
    func_ptr = ctypes.c_void_p.from_address(
        vtbl + index * ctypes.sizeof(ctypes.c_void_p)
    ).value
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(func_ptr)


def _release(obj_addr: int) -> None:
    _vtbl_call(obj_addr, _VTBL_RELEASE, ctypes.c_ulong)(obj_addr)
