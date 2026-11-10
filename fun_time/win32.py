from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app_support.win32_loader import load_dll, win_functype

from fun_time.dashboard_layout import Rect
from fun_time.win32_process import list_child_pids

logger = logging.getLogger(__name__)

_user32 = load_dll("user32")
_kernel32 = load_dll("kernel32")


class Win32Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("lower", ctypes.c_long),
    ]


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
IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
ICON_SMALL, ICON_BIG = 0, 1
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x00000002
GW_HWNDNEXT = 2

# Declare argtypes so ctypes passes HWND parameters as 64-bit pointers.
# Without this, ctypes defaults to c_int (32-bit) for Python ints, which
# corrupts the sentinel HWND_TOPMOST/HWND_NOTOPMOST values on 64-bit.
_user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.wintypes.UINT,
]
_user32.SetWindowPos.restype = ctypes.wintypes.BOOL

WNDENUMPROC = win_functype(
    ctypes.wintypes.BOOL,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)


WM_CLOSE = 0x0010


def close_window(hwnd: int) -> None:
    if not hwnd:
        return
    _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _first_window(match: Callable[[int], bool]) -> int:
    found: int = 0

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal found
        if not match(hwnd):
            return True
        found = hwnd
        return False

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def _every_window(match: Callable[[int], bool]) -> set[int]:
    found: set[int] = set()

    def callback(hwnd: int, _lparam: int) -> bool:
        if match(hwnd):
            found.add(hwnd)
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def window_class(hwnd: int) -> str:
    name = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, name, 256)
    return name.value


def window_title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    title = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, title, length + 1)
    return title.value


def _has_a_title(hwnd: int) -> bool:
    return _user32.GetWindowTextLengthW(hwnd) > 0


def find_windows_by_class(substring: str) -> set[int]:
    # A browser window carries the page's title and its launcher handed the URL to a running Chrome,
    # so only the window class finds it.
    return _every_window(
        lambda hwnd: bool(_user32.IsWindowVisible(hwnd))
        and _has_a_title(hwnd)
        and substring in window_class(hwnd))


def find_window_by_pid(pid: int, *, include_hidden: bool = False) -> int:
    def matches(hwnd: int) -> bool:
        if window_process(hwnd) != pid:
            return False
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
            return False
        return _has_a_title(hwnd)

    return _first_window(matches)


def find_window_for_process(
    pid: int, title: str | Callable[[str], bool] | None = None, *, include_hidden: bool = False
) -> int:
    # On Windows a venv's Scripts\python.exe spawns the interpreter that owns the windows as a child, so a
    # recorded pid can be a launcher's.
    if not pid:
        return 0
    pids_one_launcher_deep = {pid, *list_child_pids(pid)}

    def matches(hwnd: int) -> bool:
        if window_process(hwnd) not in pids_one_launcher_deep:
            return False
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
            return False
        caption = window_title(hwnd)
        if not caption:
            return False
        if title is None:
            return True
        return title(caption) if callable(title) else caption == title

    return _first_window(matches)


def wait_for_window_by_title(
    title: str, timeout_s: float = 5.0, *, exact: bool = False, include_hidden: bool = False
) -> int:
    return wait_for_window(
        lambda: find_window_by_title(title, exact=exact, include_hidden=include_hidden),
        timeout_s,
    )


def wait_for_window(find: Callable[[], int], timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if hwnd := find():
            return hwnd
        time.sleep(0.1)
    return 0


STALLED_WINDOW_TIMEOUT_S = 1.5


def window_process(hwnd: int) -> int:
    pid = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _owned_by_this_process(hwnd: int) -> bool:
    return window_process(hwnd) == _kernel32.GetCurrentProcessId()


def _without_hanging(call, hwnd, *args, what: str) -> bool:
    if _owned_by_this_process(hwnd):
        call(hwnd, *args)
        return True

    if _ONE_CALL_AT_A_TIME.make(hwnd, lambda: call(hwnd, *args), what=what).wait(
            STALLED_WINDOW_TIMEOUT_S):
        return True
    logger.warning(
        "%s did not return in %.1fs — that window has stopped answering; "
        "carrying on without it", what, STALLED_WINDOW_TIMEOUT_S,
    )
    return False


class _CallsPerWindow:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: dict[int, deque[tuple[Callable[[], object], threading.Event, str]]] = {}

    def make(self, hwnd: int, call: Callable[[], object], *, what: str) -> threading.Event:
        done = threading.Event()
        with self._lock:
            first = hwnd not in self._waiting
            self._waiting.setdefault(hwnd, deque()).append((call, done, what))
        if first:
            threading.Thread(target=self._make_in_turn, args=(hwnd,), daemon=True,
                             name=f"win32-calls-to-{hwnd}").start()
        return done

    def _make_in_turn(self, hwnd: int) -> None:
        finished = False
        while not finished:
            with self._lock:
                call, done, what = self._waiting[hwnd][0]
            try:
                call()
            except Exception:
                logger.exception("%s failed", what)
            with self._lock:
                self._waiting[hwnd].popleft()
                finished = not self._waiting[hwnd]
                if finished:
                    del self._waiting[hwnd]
            done.set()


_ONE_CALL_AT_A_TIME = _CallsPerWindow()


def move_window(hwnd: int, rect: Rect, *, activate: bool = True) -> None:
    _without_hanging(
        _user32.ShowWindow, hwnd, SW_RESTORE if activate else SW_SHOWNOACTIVATE,
        what=f"ShowWindow({hwnd})",
    )
    _without_hanging(
        _user32.SetWindowPos, hwnd, 0, *rect, SWP_NOZORDER | SWP_NOACTIVATE,
        what=f"SetWindowPos({hwnd})",
    )


def place_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    _without_hanging(
        _user32.SetWindowPos, hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE,
        what=f"place_window({hwnd})",
    )


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = Win32Rect()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.lower - rect.top


def set_always_on_top(hwnd: int, on_top: bool, *, under: int = 0) -> None:
    # Windows' SetWindowPos waits for the target's own thread, which is why this goes through
    # _without_hanging.
    if not on_top:
        insert_after = HWND_NOTOPMOST
    elif under:
        insert_after = ctypes.wintypes.HWND(under)
    else:
        insert_after = HWND_TOPMOST
    _stack_after(hwnd, insert_after, what=f"set_always_on_top({hwnd}, {on_top})")


def _stack_after(hwnd: int, insert_after, *, what: str) -> None:
    _without_hanging(
        _user32.SetWindowPos, hwnd, insert_after, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE, what=what,
    )


def is_window_topmost(hwnd: int) -> bool:
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    return bool(ex_style & WS_EX_TOPMOST)


def keep_in_topmost_band(hwnd: int, *, topmost: bool) -> None:
    if is_window_topmost(hwnd) != topmost:
        set_always_on_top(hwnd, topmost)


@dataclass(frozen=True)
class StackedWindow:
    hwnd: int
    title: str
    topmost: bool
    rect: tuple[int, int, int, int]


# Framed windows carry an INVISIBLE resize border (~7-8px per edge on Windows
# 10/11) that GetWindowRect includes: a maximized Chrome reports itself 8px
# onto the neighboring monitor, and a docked dashboard reports 8px into the
# Funestra beside it.  An intersection this thin is that ghost frame, not
# anything the eye can see covered, so the overlap test ignores it.
_FRAME_GHOST_PX = 12


def _rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap_w = min(ax + aw, bx + bw) - max(ax, bx)
    overlap_h = min(ay + ah, by + bh) - max(ay, by)
    return overlap_w > _FRAME_GHOST_PX and overlap_h > _FRAME_GHOST_PX


def windows_obscuring(
    target_hwnd: int, stack: list[StackedWindow]
) -> list[StackedWindow]:
    idx = next((i for i, w in enumerate(stack) if w.hwnd == target_hwnd), None)
    if idx is None:
        return []
    target_rect = stack[idx].rect
    return [w for w in stack[:idx] if _rects_overlap(w.rect, target_rect)]


def describe_windows(windows: list[StackedWindow]) -> str:
    return "; ".join(f"{w.title!r} hwnd={w.hwnd} topmost={w.topmost} rect={w.rect}" for w in windows)


def iter_zorder() -> list[StackedWindow]:
    out: list[StackedWindow] = []
    hwnd = _user32.GetTopWindow(0)
    while hwnd:
        if _user32.IsWindowVisible(hwnd) and not _user32.IsIconic(hwnd):
            title = window_title(hwnd)
            if title:
                rect = Win32Rect()
                _user32.GetWindowRect(hwnd, ctypes.byref(rect))
                out.append(
                    StackedWindow(
                        hwnd=hwnd,
                        title=title,
                        topmost=is_window_topmost(hwnd),
                        rect=(rect.left, rect.top, rect.right - rect.left, rect.lower - rect.top),
                    )
                )
        hwnd = _user32.GetWindow(hwnd, GW_HWNDNEXT)
    return out


def activate_window(hwnd: int) -> None:
    _user32.SetForegroundWindow(hwnd)


_user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
_user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND


def foreground_window() -> int:
    return int(_user32.GetForegroundWindow() or 0)


GA_ROOT = 2
_user32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
_user32.GetCursorPos.restype = ctypes.wintypes.BOOL
_user32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
_user32.WindowFromPoint.restype = ctypes.wintypes.HWND
_user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
_user32.GetAncestor.restype = ctypes.wintypes.HWND


def window_under_cursor() -> int:
    cursor = ctypes.wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(cursor)):
        return 0
    return int(_user32.GetAncestor(_user32.WindowFromPoint(cursor), GA_ROOT) or 0)


# argtypes matter on 64-bit: without them ctypes marshals the HWND as a 32-bit
# c_int and truncates the handle, and the process-id out-param pointer must be a
# real pointer (same reasoning as the SetWindowPos/OpenProcess declarations).
_user32.GetWindowThreadProcessId.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
_user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD


def find_window_by_title(title: str, *, exact: bool = False, include_hidden: bool = False) -> int:
    title_text = ctypes.create_unicode_buffer(256)

    def matches(hwnd: int) -> bool:
        if not include_hidden and not _user32.IsWindowVisible(hwnd):
            return False
        _user32.GetWindowTextW(hwnd, title_text, 256)
        return title_text.value == title if exact else title in title_text.value

    return _first_window(matches)


SW_MINIMIZE = 6
SW_SHOWMINNOACTIVE = 7


def minimize_window(hwnd: int, *, activate_the_next_window: bool = True) -> None:
    _without_hanging(
        _user32.ShowWindow, hwnd, SW_MINIMIZE if activate_the_next_window else SW_SHOWMINNOACTIVE,
        what=f"minimize_window({hwnd})",
    )


def draw_nothing_at_all(hwnd: int) -> None:
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
    _user32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)


def set_the_icon_alt_tab_draws(hwnd: int, icon_path: str | Path) -> None:
    try:
        for which, side in ((ICON_SMALL, 16), (ICON_BIG, 32)):
            handle = _user32.LoadImageW(
                None, str(icon_path), IMAGE_ICON, side, side, LR_LOADFROMFILE)
            if handle:
                _user32.SendMessageW(hwnd, WM_SETICON, which, handle)
    except OSError:
        logger.debug("Could not set the window icon", exc_info=True)


def hide_window(hwnd: int) -> None:
    _without_hanging(_user32.ShowWindow, hwnd, SW_HIDE, what=f"hide_window({hwnd})")


def restore_window(hwnd: int, *, activate: bool = True) -> None:
    _without_hanging(
        _user32.ShowWindow, hwnd, SW_RESTORE if activate else SW_SHOWNOACTIVATE,
        what=f"restore_window({hwnd})",
    )


def show_own_window(hwnd: int) -> None:
    _user32.ShowWindow(hwnd, SW_SHOW)


def hide_own_window_without_animation(hwnd: int) -> None:
    _user32.ShowWindow(hwnd, SW_HIDE)


def set_taskbar_window_styles(hwnd: int) -> None:
    # Windows has the styles but has not redrawn the frame until a frame-changed SetWindowPos.
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


WS_POPUP = 0x80000000
WS_EX_NOACTIVATE = 0x08000000
_user32.CreateWindowExW.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.HWND, ctypes.wintypes.HMENU, ctypes.wintypes.HINSTANCE,
    ctypes.wintypes.LPVOID,
]
_user32.CreateWindowExW.restype = ctypes.wintypes.HWND


def create_the_topmost_floor_a_promotion_beneath_needs() -> int:
    return int(_user32.CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, "STATIC", None,
        WS_POPUP, 0, 0, 0, 0, None, None, None, None) or 0)


def is_window_minimized(hwnd: int) -> bool:
    return bool(_user32.IsIconic(hwnd))
