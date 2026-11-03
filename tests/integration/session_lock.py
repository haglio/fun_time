from __future__ import annotations

import _winapi
import contextlib
import ctypes
import threading
from collections.abc import Callable, Iterator
from ctypes import wintypes
from enum import Enum, auto
from time import sleep
from typing import NamedTuple

from app_support.win32_loader import load_dll

INTEGRATION_LOCK_NAME = r"Global\fun_time_integration_run"

FULL_RUN_GIVES_WAY_S = 20 * 60


class Place(Enum):
    AHEAD_OF_FULL_RUNS = auto()
    AFTER_THE_FULL_RUN_GOING_NEXT = auto()
    BEHIND_OTHER_FULL_RUNS = auto()
    GIVING_WAY_TO_SHORT_RUNS = auto()
    GOING_NEXT = auto()


class Waiting(NamedTuple):
    seconds: float
    place: Place

_WAIT_OBJECT_0 = 0x00000000
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

_kernel32.CreateSemaphoreW.restype = wintypes.HANDLE
_kernel32.CreateSemaphoreW.argtypes = [wintypes.LPVOID, wintypes.LONG, wintypes.LONG, wintypes.LPCWSTR]

_kernel32.ReleaseSemaphore.restype = wintypes.BOOL
_kernel32.ReleaseSemaphore.argtypes = [wintypes.HANDLE, wintypes.LONG, ctypes.POINTER(wintypes.LONG)]

_MOST_NUMBERS = 0x7FFFFFFF


class SingleInstanceLock:
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
        handle = self._ensure_handle()
        if timeout is None:
            result = _winapi.WaitForMultipleObjects([handle], False, _INFINITE)
        else:
            result = _kernel32.WaitForSingleObject(handle, max(0, int(timeout * 1000)))
            if result == _WAIT_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
        self._held = result in (_WAIT_OBJECT_0, _WAIT_ABANDONED)
        return self._held

    def release(self) -> None:
        if self._held and self._handle is not None:
            _kernel32.ReleaseMutex(self._handle)
            self._held = False

    def close(self) -> None:
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
    line = _Line(name)
    try:
        line.join(short=short)
        if short:
            with _saying_it_waits(notify, notify_every_s, _ShortRunsPlace(name)):
                line.wait_as_a_short_run()
        else:
            patience = _Patience(name, full_run_gives_way_s, notify_every_s)
            with _saying_it_waits(notify, notify_every_s, patience):
                line.wait_as_a_full_run(patience)
        yield line.run
    finally:
        line.close()


_A_FULL_RUN_STEPS_ASIDE_S = 0.05


class _Line:
    def __init__(self, name: str) -> None:
        self.run = SingleInstanceLock(name)
        self._short_lane = _Lane(f"{name}_short_lane")
        self._full_lane = _Lane(f"{name}_full_lane")
        self._full_goes_next = SingleInstanceLock(f"{name}_full_goes_next")
        self._short_running = SingleInstanceLock(f"{name}_short_running")

    def join(self, *, short: bool) -> None:
        (self._short_lane if short else self._full_lane).join()

    def wait_as_a_short_run(self) -> None:
        self._short_lane.wait_for_the_front()
        while True:
            if _held_elsewhere(self._full_goes_next):
                self._full_goes_next.acquire()
                self._full_goes_next.release()
            self.run.acquire()
            if not _held_elsewhere(self._full_goes_next):
                break
            self.run.release()
        self._short_running.acquire()
        self._short_lane.leave()

    def wait_as_a_full_run(self, patience: _Patience) -> None:
        self._full_lane.wait_for_the_front()
        patience.heads_the_full_runs()
        while True:
            self.run.acquire()
            if patience.spent() or not _held_elsewhere(self._short_lane.front):
                break
            self.run.release()
            sleep(_A_FULL_RUN_STEPS_ASIDE_S)
        self._full_lane.leave()

    def close(self) -> None:
        self._short_running.close()
        self._short_lane.leave()
        self._full_lane.leave()
        self._full_goes_next.close()
        self.run.close()


class _Lane:
    def __init__(self, name: str) -> None:
        self.front = SingleInstanceLock(name)
        self._name = name
        self._numbers = _Numbers(f"{name}_numbers")
        self._number = 0
        self._place: SingleInstanceLock | None = None

    def join(self) -> None:
        with SingleInstanceLock(f"{self._name}_joining"):
            self._number = self._numbers.take()
            self._place = self._place_of(self._number)
            self._place.acquire()

    def wait_for_the_front(self) -> None:
        for earlier in range(self._number):
            with self._place_of(earlier):
                pass
        self.front.acquire()

    def leave(self) -> None:
        self.front.close()
        if self._place is not None:
            self._place.close()
        self._numbers.close()

    def _place_of(self, number: int) -> SingleInstanceLock:
        return SingleInstanceLock(f"{self._name}_{number}")


class _Numbers:
    def __init__(self, name: str) -> None:
        self._name = name
        self._handle: int | None = None

    def take(self) -> int:
        if self._handle is None:
            handle = _kernel32.CreateSemaphoreW(None, 0, _MOST_NUMBERS, self._name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = handle
        count_before = wintypes.LONG()
        if not _kernel32.ReleaseSemaphore(self._handle, 1, ctypes.byref(count_before)):
            raise ctypes.WinError(ctypes.get_last_error())
        return count_before.value

    def close(self) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


class _Patience:
    def __init__(self, name: str, gives_way_s: float, looks_every_s: float) -> None:
        self._short_running = SingleInstanceLock(f"{name}_short_running")
        self._goes_next = SingleInstanceLock(f"{name}_full_goes_next")
        self._gives_way_s = gives_way_s
        self._looks_every_s = looks_every_s
        self._given_way = 0.0
        self._going_next = False
        self._at_the_head = threading.Event()

    def heads_the_full_runs(self) -> None:
        self._at_the_head.set()

    def spent(self) -> bool:
        return self._given_way >= self._gives_way_s

    def waited(self) -> Place:
        if not self._at_the_head.is_set():
            return Place.BEHIND_OTHER_FULL_RUNS
        if _held_elsewhere(self._short_running):
            self._given_way += self._looks_every_s
        if self.spent() and not self._going_next:
            self._going_next = self._goes_next.acquire(timeout=0)
        return Place.GOING_NEXT if self._going_next else Place.GIVING_WAY_TO_SHORT_RUNS

    def close(self) -> None:
        self._goes_next.close()
        self._short_running.close()


class _ShortRunsPlace:
    def __init__(self, name: str) -> None:
        self._full_goes_next = SingleInstanceLock(f"{name}_full_goes_next")

    def waited(self) -> Place:
        if _held_elsewhere(self._full_goes_next):
            return Place.AFTER_THE_FULL_RUN_GOING_NEXT
        return Place.AHEAD_OF_FULL_RUNS

    def close(self) -> None:
        self._full_goes_next.close()


def _held_elsewhere(lock: SingleInstanceLock) -> bool:
    if lock.acquire(timeout=0):
        lock.release()
        return False
    return True


@contextlib.contextmanager
def _saying_it_waits(notify: Callable[[Waiting], None] | None, every_s: float,
                     place: _Patience | _ShortRunsPlace) -> Iterator[None]:
    stop = threading.Event()

    def say() -> None:
        waited = 0.0
        try:
            while not stop.wait(every_s):
                waited += every_s
                where = place.waited()
                if notify is not None:
                    notify(Waiting(waited, where))
        finally:
            place.close()

    sayer = threading.Thread(target=say, name="integration-queue-notice", daemon=True)
    sayer.start()
    try:
        yield
    finally:
        stop.set()
        sayer.join()
