"""A machine-wide single-instance lock for serializing integration runs.

Multiple worktree agents share this repo and may launch the integration suite
at the same time.  Every run launches VLC/Nau/AHK and runs a global name+age
process sweep (``FunTimeIntegrationSession._kill_recent_runtime_processes``) that
force-kills *any* recent AutoHotkey64/pythonw/vlc — including a concurrent run's
freshly-spawned processes — and the AHK bridge runs under ``#SingleInstance
Force`` so a second bridge launch evicts the first.  The result is flaky,
non-deterministic failures (different tests each run) whenever two suites overlap.

``SingleInstanceLock`` wraps a Windows *named mutex*: a kernel object keyed by
name with machine-global scope (the ``Global\\`` prefix).  Because the name
resolves to the same kernel object across every process on the machine, only one
holder — across all agents/processes — can own it at a time; the rest block until
it is free.  Crucially, if the owning process dies without releasing (crash,
kill, power loss) the OS marks the mutex *abandoned*, and the next waiter's
``WaitForSingleObject`` returns ``WAIT_ABANDONED`` and is granted ownership.  So a
dead run can never deadlock the queue — no PID-liveness polling or heartbeat file
is needed; the kernel provides recovery for free.
"""
from __future__ import annotations

import contextlib
import ctypes
import sys
from collections.abc import Callable, Iterator

# The one machine-wide name every integration run contends on.  ``Global\`` puts
# it in the system-wide namespace so runs in different login sessions still
# serialize against each other on the same machine.
INTEGRATION_LOCK_NAME = r"Global\fun_time_integration_run"

# WaitForSingleObject return codes (winbase.h).
_WAIT_OBJECT_0 = 0x00000000
# The previous owner died without releasing; the OS grants us ownership anyway.
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF

if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    _kernel32.ReleaseMutex.restype = wintypes.BOOL
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]

    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
else:  # pragma: no cover - the integration suite is Windows-only
    _kernel32 = None


class SingleInstanceLock:
    """A machine-wide mutual-exclusion lock backed by a Windows named mutex."""

    def __init__(self, name: str = INTEGRATION_LOCK_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._held = False

    def _ensure_handle(self) -> int:
        if self._handle is None:
            handle = _kernel32.CreateMutexW(None, False, self._name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = handle
        return self._handle

    def acquire(self, timeout: float | None = None) -> bool:
        """Block until the lock is held; return ``True`` once it is.

        ``timeout`` is seconds to wait; ``None`` waits forever (queue behavior).
        Returns ``False`` if the timeout elapses before the lock is acquired.
        """
        handle = self._ensure_handle()
        millis = _INFINITE if timeout is None else max(0, int(timeout * 1000))
        result = _kernel32.WaitForSingleObject(handle, millis)
        last_error = ctypes.get_last_error()
        # WAIT_ABANDONED means a prior holder crashed without releasing; the OS
        # still hands us ownership, so it is a successful (recovering) acquire.
        if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            self._held = True
            return True
        if result == _WAIT_TIMEOUT:
            return False
        raise ctypes.WinError(last_error)

    def release(self) -> None:
        """Relinquish ownership so a waiting caller can acquire the lock."""
        if self._held and self._handle is not None:
            _kernel32.ReleaseMutex(self._handle)
            self._held = False

    def close(self) -> None:
        """Release (if held) and drop the OS handle."""
        self.release()
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@contextlib.contextmanager
def hold_integration_lock(
    *,
    name: str = INTEGRATION_LOCK_NAME,
    poll_seconds: float = 2.0,
    notify: Callable[[float], None] | None = None,
) -> Iterator[SingleInstanceLock]:
    """Hold the machine-wide integration lock for the duration of the block.

    Blocks until the lock is free — other runs queue here instead of clobbering
    — then releases it on exit even if the block raises.  While waiting, calls
    ``notify(total_seconds_waited)`` after each poll interval so a queuing run
    can surface that it is waiting rather than hanging silently.
    """
    lock = SingleInstanceLock(name)
    waited = 0.0
    while not lock.acquire(timeout=poll_seconds):
        waited += poll_seconds
        if notify is not None:
            notify(waited)
    try:
        yield lock
    finally:
        lock.close()
