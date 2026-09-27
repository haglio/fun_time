"""A machine-wide single-instance lock for serializing integration runs.

Multiple worktree agents share this repo and may launch the integration suite
at the same time.  Every run launches the players/AHK and reaps leftover app
processes (``FunTimeIntegrationSession._reap_leftover_runtime_processes``) that
force-kills *any* AutoHotkey64/pythonw it finds — including a concurrent
run's freshly-spawned processes — and the AHK bridge runs under ``#SingleInstance
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

A named mutex goes to the run that has waited on it longest, so a run that
waits in place keeps its place.  Each kind of run therefore waits in a lane of
its own -- one more named mutex, waited on in place -- and only the run at the
front of each lane waits on the lock itself, where the front full run steps
aside for the front short run until its patience is spent.  The lanes and the
flags between them are named mutexes for the lock's own reason: a run that dies
leaves them abandoned, and the next run to ask is granted an abandoned mutex,
so a dead run holds nobody back.
"""
from __future__ import annotations

import _winapi
import contextlib
import ctypes
import threading
from collections.abc import Callable, Iterator
from ctypes import wintypes
from time import sleep
from typing import NamedTuple

from fun_time.win32_loader import load_dll

# The one machine-wide name every integration run contends on.  ``Global\`` puts
# it in the system-wide namespace so runs in different login sessions still
# serialize against each other on the same machine.
INTEGRATION_LOCK_NAME = r"Global\fun_time_integration_run"

FULL_RUN_GIVES_WAY_S = 20 * 60


class Waiting(NamedTuple):
    seconds: float
    goes_next: bool = False

# WaitForSingleObject return codes (winbase.h).
_WAIT_OBJECT_0 = 0x00000000
# The previous owner died without releasing; the OS grants us ownership anyway.
_WAIT_ABANDONED = 0x00000080
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF

_kernel32 = load_dll("kernel32", use_last_error=True)

_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

_kernel32.ReleaseMutex.restype = wintypes.BOOL
_kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]

_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


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
        if timeout is None:
            result = _winapi.WaitForMultipleObjects([handle], False, _INFINITE)
        else:
            result = _kernel32.WaitForSingleObject(handle, max(0, int(timeout * 1000)))
            if result == _WAIT_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
        # WAIT_ABANDONED means a prior holder crashed without releasing; the OS
        # still hands us ownership, so it is a successful (recovering) acquire.
        self._held = result in (_WAIT_OBJECT_0, _WAIT_ABANDONED)
        return self._held

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

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@contextlib.contextmanager
def hold_integration_lock(
    *,
    name: str = INTEGRATION_LOCK_NAME,
    short: bool = False,
    notify_every_s: float = 2.0,
    full_run_gives_way_s: float = FULL_RUN_GIVES_WAY_S,
    notify: Callable[[Waiting], None] | None = None,
) -> Iterator[SingleInstanceLock]:
    """Hold the machine-wide integration lock for the duration of the block.

    Blocks until the lock is free — other runs queue here, in the order they
    arrived, instead of clobbering — then releases it on exit even if the block
    raises.  A *short* run goes ahead of full runs; a full run lets short runs
    go first until they have held the lock for *full_run_gives_way_s* while it
    waited, and from then on they wait for it.  While waiting, calls ``notify``
    every *notify_every_s* so a queuing run can surface that it is waiting
    rather than hanging silently.
    """
    line = _Line(name)
    patience = None if short else _Patience(name, full_run_gives_way_s)
    try:
        with _saying_it_waits(notify, notify_every_s, patience):
            if patience is None:
                line.wait_as_a_short_run()
            else:
                line.wait_as_a_full_run(patience)
        yield line.run
    finally:
        line.close()


_A_FULL_RUN_STEPS_ASIDE_S = 0.05


class _Line:
    def __init__(self, name: str) -> None:
        self.run = SingleInstanceLock(name)
        self._short_lane = SingleInstanceLock(f"{name}_short_lane")
        self._full_lane = SingleInstanceLock(f"{name}_full_lane")
        self._full_goes_next = SingleInstanceLock(f"{name}_full_goes_next")
        self._short_running = SingleInstanceLock(f"{name}_short_running")

    def wait_as_a_short_run(self) -> None:
        self._short_lane.acquire()
        while True:
            if _held_elsewhere(self._full_goes_next):
                self._full_goes_next.acquire()
                self._full_goes_next.release()
            self.run.acquire()
            if not _held_elsewhere(self._full_goes_next):
                break
            self.run.release()
        self._short_running.acquire()
        self._short_lane.release()

    def wait_as_a_full_run(self, patience: _Patience) -> None:
        self._full_lane.acquire()
        patience.heads_the_full_runs()
        while True:
            self.run.acquire()
            if patience.spent() or not _held_elsewhere(self._short_lane):
                break
            self.run.release()
            sleep(_A_FULL_RUN_STEPS_ASIDE_S)
        self._full_lane.release()

    def close(self) -> None:
        for lock in (self._short_running, self._short_lane, self._full_lane, self._full_goes_next,
                     self.run):
            lock.close()


class _Patience:
    def __init__(self, name: str, gives_way_s: float) -> None:
        self._short_running = SingleInstanceLock(f"{name}_short_running")
        self._goes_next = SingleInstanceLock(f"{name}_full_goes_next")
        self._gives_way_s = gives_way_s
        self._given_way = 0.0
        self._going_next = False
        self._at_the_head = threading.Event()

    def heads_the_full_runs(self) -> None:
        self._at_the_head.set()

    def spent(self) -> bool:
        return self._given_way >= self._gives_way_s

    def waited(self, seconds: float) -> bool:
        if _held_elsewhere(self._short_running):
            self._given_way += seconds
        if self.spent() and self._at_the_head.is_set() and not self._going_next:
            self._going_next = self._goes_next.acquire(timeout=0)
        return self._going_next

    def close(self) -> None:
        self._goes_next.close()
        self._short_running.close()


def _held_elsewhere(lock: SingleInstanceLock) -> bool:
    if lock.acquire(timeout=0):
        lock.release()
        return False
    return True


@contextlib.contextmanager
def _saying_it_waits(notify: Callable[[Waiting], None] | None, every_s: float,
                     patience: _Patience | None) -> Iterator[None]:
    stop = threading.Event()

    def say() -> None:
        waited = 0.0
        try:
            while not stop.wait(every_s):
                waited += every_s
                goes_next = patience is not None and patience.waited(every_s)
                if notify is not None:
                    notify(Waiting(waited, goes_next))
        finally:
            if patience is not None:
                patience.close()

    sayer = threading.Thread(target=say, name="integration-queue-notice", daemon=True)
    sayer.start()
    try:
        yield
    finally:
        stop.set()
        sayer.join()
