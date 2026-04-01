"""Single-instance guards using Win32 named mutexes."""
from __future__ import annotations

import ctypes

_kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
_user32 = ctypes.windll.user32  # type: ignore[attr-defined]

ERROR_ALREADY_EXISTS = 183

MUTEX_BROKER = "Global\\FunTime.Broker"
MUTEX_ORCHESTRATOR = "Global\\FunTime.Orchestrator"


def try_acquire_mutex(name: str) -> int | None:
    """Try to create a named mutex.

    Returns the handle if this is the first instance, or ``None`` if
    another instance already holds it.  The caller must keep the
    returned handle alive for the process lifetime.
    """
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if _kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


MB_OK = 0x0
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x00010000


def show_already_running_message(text: str, title: str = "Fun Time") -> None:
    """Show a MessageBox informing the user another instance is running."""
    _user32.MessageBoxW(None, text, title, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND)
