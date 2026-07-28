"""Single-instance guards using Win32 named mutexes."""
from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path

# use_last_error=True makes ctypes save/restore the per-thread error code
# around each call, preventing Python's runtime from clobbering it.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
_user32 = ctypes.windll.user32  # type: ignore[attr-defined]
_get_last_error = ctypes.get_last_error

ERROR_ALREADY_EXISTS = 183

MUTEX_BROKER = "Global\\FunTime.Broker"
MUTEX_ORCHESTRATOR = "Global\\FunTime.Orchestrator"


def mutex_name_for_config(base: str, instance_id: str | Path) -> str:
    """Derive a mutex name from a base prefix and a session identity.

    The identity is ``ProjectConfig.instance_id`` — the config path, unless the
    config names another session's.  The same identity always produces the same
    mutex, so the real app (single config) blocks duplicates while integration
    tests (unique tmp configs) run without conflict, and a branch-verification
    session — which borrows the live session's identity on purpose — is refused
    while that one is up, in either order.
    """
    suffix = hashlib.md5(str(instance_id).encode()).hexdigest()[:12]
    return f"{base}.{suffix}"


def try_acquire_mutex(name: str) -> int | None:
    """Try to create a named mutex.

    Returns the handle if this is the first instance, or ``None`` if
    another instance already holds it.  The caller must keep the
    returned handle alive for the process lifetime.
    """
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if _get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


MB_OK = 0x0
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x00010000


def show_already_running_message(text: str, title: str = "Fun Time") -> None:
    """Show a MessageBox informing the user another instance is running."""
    _user32.MessageBoxW(None, text, title, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND)
