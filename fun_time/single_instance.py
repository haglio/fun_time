"""Which mutex says a session is running, what claims it, and what a second
launch is told."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from fun_time.win32_loader import load_dll

# ``app_support.win32.mutex_name`` adds the session's identity, so a branch
# session, which borrows the live one's, refuses it and is refused by it.  The
# name cannot change without letting a second session start beside one.
MUTEX_ORCHESTRATOR = "Global\\FunTime.Orchestrator"


_ERROR_ALREADY_EXISTS = 183
_WAIT_TIMEOUT = 0x00000102


def _kernel32():
    dll = load_dll("kernel32", use_last_error=True)
    dll.CreateMutexW.restype = ctypes.c_void_p
    dll.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    dll.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    dll.CloseHandle.argtypes = [ctypes.c_void_p]
    return dll


def claim_the_session(name: str) -> int | None:
    """The handle that says this process is the session, or None where another
    live one holds *name*.  The caller keeps it for as long as the session runs.
    """
    dll = _kernel32()
    handle = dll.CreateMutexW(None, True, name)
    if not handle:
        return None
    if ctypes.get_last_error() != _ERROR_ALREADY_EXISTS:
        return handle
    if dll.WaitForSingleObject(ctypes.c_void_p(handle), 0) == _WAIT_TIMEOUT:
        dll.CloseHandle(ctypes.c_void_p(handle))  # a live session owns it
        return None
    return handle


def let_the_session_go(handle: int | None) -> None:
    if handle:
        _kernel32().CloseHandle(ctypes.c_void_p(handle))


def show_already_running_message(text: str, title: str = "Fun Time") -> None:
    """Say another instance holds the mutex, in Fun Time's own colors."""
    from shared_ui.alert import Level, show_alert

    from fun_time.project_paths import PROJECT_ICON

    show_alert(title, text, level=Level.INFO, icon=PROJECT_ICON)
