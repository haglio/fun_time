"""Win32 window operations for the Python orchestrator.

Wraps the ctypes calls the startup sequencer needs to manage the session's
windows: find or wait for one by pid or title, move it, set its topmost band,
activate it, minimize and restore it, read its rect, and walk the z-order.
Every cross-process call goes through :func:`_without_hanging`, for the reason
that function's docstring records.

The two subsystems this file used to also hold are next door:
:mod:`fun_time.win32_process` asks about processes rather than windows, and
:mod:`fun_time.win32_taskbar` carries the AppUserModelID work.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fun_time.win32_loader import load_dll, win_functype
from fun_time.win32_process import list_child_pids

logger = logging.getLogger(__name__)

_user32 = load_dll("user32")
_kernel32 = load_dll("kernel32")
_dwmapi = load_dll("dwmapi")


# Constants
SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = ctypes.wintypes.HWND(-1)
HWND_NOTOPMOST = ctypes.wintypes.HWND(-2)
SWP_FRAMECHANGED = 0x0020
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_EX_TOPMOST = 0x00000008
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SW_HIDE = 0
SW_SHOW = 5
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

WNDENUMPROC = win_functype(
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


def _first_window(match: Callable[[int], bool]) -> int:
    """The first top-level window *match* accepts, or 0 if none does.

    ``EnumWindows`` stops when its callback returns False, so every lookup that
    wants ONE window carried the same prototype, ``nonlocal`` and inverted
    return.  Three did; this holds them, and each keeps only its predicate —
    including how it reads a title, which the two do differently on purpose.
    """
    found: int = 0

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal found
        if not match(hwnd):
            return True  # keep enumerating
        found = hwnd
        return False  # stop enumeration

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


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
    def matches(hwnd: int) -> bool:
        window_pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid:
            return False
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
            return False
        # Non-empty title only (skip internal/unnamed windows)
        return _user32.GetWindowTextLengthW(hwnd) > 0

    return _first_window(matches)


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

    def matches(hwnd: int) -> bool:
        window_pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value not in pids:
            return False
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return False
        # Per window, at its own length; by-title shares one 256.
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value == title

    return _first_window(matches)


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
    messages to the thread that owns the window and waits for it to handle them,
    with no timeout and no flag on our side that changes that — so a player
    whose own loop has stalled blocks the caller forever.  One did, and took the
    session with it; ``TestAWindowThatHasStoppedAnswering`` tells that story and
    holds every rule below.

    So the call is made on a throwaway thread and waited on for
    HUNG_WINDOW_TIMEOUT_S.  A healthy window answers in microseconds and nothing
    changes — including the ORDER the caller makes these calls in, which is what
    stacks Genau's HUD above Nau's video and which posting the requests
    (SWP_ASYNCWINDOWPOS) would have given up.  A window that does not answer is
    named in the log and left where it is.  Its worker stays blocked in the
    kernel until that window's owner recovers or dies: one leaked thread per
    call to a hung window, against a wedged session.

    Our OWN windows are called straight, and must be: the send would go to this
    process's UI thread, which is the very thread waiting here.  See
    ``show_own_window`` and the section below it.
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


def keep_in_topmost_band(hwnd: int, *, topmost: bool) -> None:
    """Put *hwnd* in or out of the topmost band — only if it is not already.

    Drift correction, not assertion: three windows in this session correct their
    own band on a timer, because the orchestrator's pass over them is not
    reliable.  Running SetWindowPos unconditionally would fight whoever else
    re-asserts the flag and flicker in the steady state.
    """
    if is_window_topmost(hwnd) != topmost:
        set_always_on_top(hwnd, topmost)


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

    Windows refuses ``SetForegroundWindow`` unless the calling process owns the
    foreground window or received the last input — and the bridge is neither
    when a hotkey lands: AHK got the key, a player owns the screen.  The refusal
    is silent, and delivers no WM_ACTIVATE, which is the message the window has
    to see.  Attaching this thread's input queue to the foreground thread's is
    one of the cases the rule accepts, so the call goes through.

    Returns whether the window really ended up there.  A False is worth logging
    but not acting on: a non-input desktop (the integration suite's) has no
    foreground to be, so it reads False while the activation still lands.
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
    """The first visible window whose title contains — with *exact*, equals —
    *title*, or 0.

    Four of this session's windows are called "Fun Time" and "Fun Time
    <something>", so the panel needs *exact*.  *include_hidden* also matches a
    window with WS_VISIBLE cleared, which the dashboard is behind the cover.
    """
    buf = ctypes.create_unicode_buffer(256)  # one for the whole walk

    def matches(hwnd: int) -> bool:
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
            return False
        _user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value == title if exact else title in buf.value

    return _first_window(matches)


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


# --- A window this process owns ---
#
# The only calls here that deliberately skip _without_hanging, for the reason
# its docstring gives.  Each takes a handle this process created; nothing else
# may be passed to one.


def show_own_window(hwnd: int) -> None:
    """Show one of this process's own windows."""
    _user32.ShowWindow(hwnd, SW_SHOW)


def hide_own_window(hwnd: int) -> None:
    """Hide one, so it renders nothing at all — no flash, no animation."""
    _user32.ShowWindow(hwnd, SW_HIDE)


def set_taskbar_window_styles(hwnd: int) -> None:
    """Give one a taskbar button, and a title bar with minimize and close.

    Read-modify-write on both style words, then a frame-changed
    ``SetWindowPos``, without which Windows has the styles but has not redrawn
    the frame from them.
    """
    style = _user32.GetWindowLongW(hwnd, GWL_STYLE)
    _user32.SetWindowLongW(
        hwnd, GWL_STYLE, (style | WS_SYSMENU | WS_MINIMIZEBOX) & ~WS_MAXIMIZEBOX)
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE, (ex_style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
    _user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0,
        SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
    )


def insert_below(hwnd: int, other_hwnd: int) -> None:
    """Put one directly under *other_hwnd*, or leave the band alone given 0.

    Showing a window puts it at the TOP of its band, so one revealed while
    another topmost window must keep the screen is placed in the same call.
    """
    _user32.SetWindowPos(
        hwnd, ctypes.c_void_p(other_hwnd), 0, 0, 0, 0,
        SWP_NOSIZE | SWP_NOMOVE | SWP_FRAMECHANGED
        | (SWP_NOACTIVATE if other_hwnd else SWP_NOZORDER),
    )


def is_window_minimized(hwnd: int) -> bool:
    """True if the window is currently minimized (iconic)."""
    return bool(_user32.IsIconic(hwnd))
