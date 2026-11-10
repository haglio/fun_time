from __future__ import annotations

import ctypes
from ctypes import wintypes

from app_support.win32_loader import load_dll

from fun_time import preview_marker
from fun_time.project_paths import PROJECT_ICON

MUTEX_ORCHESTRATOR = "Global\\FunTime.Orchestrator"


_WAIT_TIMEOUT = 0x00000102


def _kernel32():
    dll = load_dll("kernel32", use_last_error=True)
    dll.CreateMutexW.restype = ctypes.c_void_p
    dll.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    dll.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    dll.ReleaseMutex.argtypes = [ctypes.c_void_p]
    dll.CloseHandle.argtypes = [ctypes.c_void_p]
    return dll


def claim_the_session(name: str) -> int | None:
    dll = _kernel32()
    handle = dll.CreateMutexW(None, False, name)
    if not handle:
        return None
    owned_by_a_live_session = dll.WaitForSingleObject(ctypes.c_void_p(handle), 0) == _WAIT_TIMEOUT
    if owned_by_a_live_session:
        dll.CloseHandle(ctypes.c_void_p(handle))
        return None
    return handle


def let_the_session_go(handle: int | None) -> None:
    if not handle:
        return
    dll = _kernel32()
    dll.ReleaseMutex(ctypes.c_void_p(handle))
    dll.CloseHandle(ctypes.c_void_p(handle))


def show_already_running_message(text: str, title: str | None = None) -> None:
    # Qt loads only for this: asking whether we may run draws nothing.
    from shared_ui.alert import Level, show_alert  # noqa: PLC0415

    shown = preview_marker.shown_as()
    show_alert(title or preview_marker.app_title(shown), text, level=Level.INFO,
               icon=preview_marker.icon_file(PROJECT_ICON, shown))
