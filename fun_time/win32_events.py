from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
from collections.abc import Callable

from app_support.win32_loader import load_dll, win_functype

from fun_time.text_field_clicks import (
    EVENT_OBJECT_DESTROY,
    EVENT_OBJECT_HIDE,
    EVENT_SYSTEM_CAPTUREEND,
    EVENT_SYSTEM_CAPTURESTART,
    DesktopEvents,
    Seen,
)

_user32 = load_dll("user32", use_last_error=True)
_kernel32 = load_dll("kernel32", use_last_error=True)

WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
GA_ROOT = 2
WM_QUIT = 0x0012
WM_USER = 0x0400
PM_NOREMOVE = 0x0000
IDC_IBEAM = 32513
CURSOR_SHOWING = 0x00000001

_HOOKED_RANGES = (
    (EVENT_SYSTEM_CAPTURESTART, EVENT_SYSTEM_CAPTUREEND),
    (EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE),
)
_STOP_WAIT_S = 2.0

_WINEVENTPROC = win_functype(
    None, ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.HWND,
    ctypes.wintypes.LONG, ctypes.wintypes.LONG, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD)


class _CursorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("ptScreenPos", ctypes.wintypes.POINT),
    ]


_user32.SetWinEventHook.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.HMODULE, _WINEVENTPROC,
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
_user32.SetWinEventHook.restype = ctypes.wintypes.HANDLE
_user32.UnhookWinEvent.argtypes = [ctypes.wintypes.HANDLE]
_user32.UnhookWinEvent.restype = ctypes.wintypes.BOOL
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG), ctypes.wintypes.HWND, ctypes.wintypes.UINT,
    ctypes.wintypes.UINT]
_user32.GetMessageW.restype = ctypes.wintypes.BOOL
_user32.PeekMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG), ctypes.wintypes.HWND, ctypes.wintypes.UINT,
    ctypes.wintypes.UINT, ctypes.wintypes.UINT]
_user32.PeekMessageW.restype = ctypes.wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
_user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
_user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL
_user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
_user32.GetAncestor.restype = ctypes.wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [
    ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
_user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CursorInfo)]
_user32.GetCursorInfo.restype = ctypes.wintypes.BOOL
_user32.LoadCursorW.argtypes = [ctypes.wintypes.HINSTANCE, ctypes.c_void_p]
_user32.LoadCursorW.restype = ctypes.wintypes.HANDLE
_kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD


def _root_of(hwnd: int) -> int:
    return _user32.GetAncestor(hwnd, GA_ROOT)


def _process_of(window: int) -> int:
    process = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(window, ctypes.byref(process))
    return process.value


def the_pointer_is_the_text_pointer() -> bool:
    info = _CursorInfo(cbSize=ctypes.sizeof(_CursorInfo))
    if not _user32.GetCursorInfo(ctypes.byref(info)):
        return False
    text_pointer = _user32.LoadCursorW(None, ctypes.c_void_p(IDC_IBEAM))
    return bool(info.flags & CURSOR_SHOWING) and info.hCursor == text_pointer


class PressAndCaretWatch:
    def __init__(self, *, text_pointer: Callable[[], bool] = the_pointer_is_the_text_pointer) -> None:
        self._events = DesktopEvents(root_of=_root_of, process_of=_process_of,
                                     text_pointer=text_pointer)
        self._callback = _WINEVENTPROC(self._on_event)
        self._listening = threading.Event()
        self._thread_id = 0
        self._thread = threading.Thread(target=self._listen, daemon=True,
                                        name="press-and-caret-watch")

    def start(self) -> None:
        self._thread.start()
        self._listening.wait()

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=_STOP_WAIT_S)

    def take(self) -> list[Seen]:
        return self._events.take()

    def _listen(self) -> None:
        message = ctypes.wintypes.MSG()
        try:
            _user32.PeekMessageW(ctypes.byref(message), None, WM_USER, WM_USER, PM_NOREMOVE)
            self._thread_id = _kernel32.GetCurrentThreadId()
            hooks = [_user32.SetWinEventHook(first, last, None, self._callback, 0, 0,
                                             WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
                     for first, last in _HOOKED_RANGES]
        finally:
            self._listening.set()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))
        finally:
            for hook in hooks:
                if hook:
                    _user32.UnhookWinEvent(hook)

    def _on_event(self, _hook, event, hwnd, id_object, _id_child, _thread, _ms) -> None:
        self._events.heard(event, hwnd, id_object)
