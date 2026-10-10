"""The seconds an integration run could have run: its waits are counted in these, not the wall's.

A run's processes run below normal so that they never compete with his live session, and
work at normal priority that holds every processor leaves them next to nothing.  Over any
stretch, the processors other work left free -- the machine's busy time less the run's own --
say how far the run could get: a whole processor free is full speed, half of one is half.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import threading
import time
from collections.abc import Callable
from typing import Protocol

from fun_time.win32_loader import load_dll

_kernel32 = load_dll("kernel32", use_last_error=True)
_kernel32.GetSystemTimes.argtypes = [ctypes.POINTER(wt.FILETIME)] * 3
_kernel32.GetSystemTimes.restype = wt.BOOL
_kernel32.QueryInformationJobObject.argtypes = [
    wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
_kernel32.QueryInformationJobObject.restype = wt.BOOL
_kernel32.GetCurrentProcess.restype = wt.HANDLE
_kernel32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
_kernel32.GetProcessTimes.restype = wt.BOOL

_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1


class _BasicAccounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wt.DWORD), ("TotalProcesses", wt.DWORD),
                ("ActiveProcesses", wt.DWORD), ("TotalTerminatedProcesses", wt.DWORD)]


def _seconds(filetime: wt.FILETIME) -> float:
    return ((filetime.dwHighDateTime << 32) | filetime.dwLowDateTime) / 10_000_000


def machine_busy_seconds() -> float:
    idle, kernel, user = wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    _kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return _seconds(kernel) - _seconds(idle) + _seconds(user)


def busy_seconds_of_job(job: int | None = None) -> float:
    """*job* None is the job this process runs in, or this process alone outside one."""
    spent = _BasicAccounting()
    if _kernel32.QueryInformationJobObject(job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                                           ctypes.byref(spent), ctypes.sizeof(spent), None):
        return (spent.TotalUserTime + spent.TotalKernelTime) / 10_000_000
    created, ended, kernel, user = (wt.FILETIME() for _ in range(4))
    _kernel32.GetProcessTimes(_kernel32.GetCurrentProcess(), ctypes.byref(created),
                              ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user))
    return _seconds(kernel) + _seconds(user)


class Clock(Protocol):
    def seconds(self) -> float: ...


class _Wall:
    def seconds(self) -> float:
        return time.monotonic()


WALL = _Wall()


class RunClock:
    def __init__(self, *, run_busy: Callable[[], float] = busy_seconds_of_job,
                 machine_busy: Callable[[], float] = machine_busy_seconds,
                 processors: int | None = None, wall: Callable[[], float] = time.monotonic) -> None:
        self._run_busy = run_busy
        self._machine_busy = machine_busy
        self._processors = processors or os.cpu_count() or 1
        self._wall = wall
        self._lock = threading.Lock()
        self._seconds = 0.0
        self._last = self._readings()

    def _readings(self) -> tuple[float, float, float]:
        return self._wall(), self._machine_busy(), self._run_busy()

    def seconds(self) -> float:
        with self._lock:
            now = self._readings()
            elapsed = now[0] - self._last[0]
            if elapsed > 0:
                others = (now[1] - self._last[1]) - (now[2] - self._last[2])
                free = self._processors - others / elapsed
                self._seconds += elapsed * min(1.0, max(0.0, free))
            self._last = now
            return self._seconds


def watch_for_a_timeout(seconds: float, on_timeout: Callable[[], None], *,
                        every_s: float = 1.0) -> Callable[[], None]:
    budget = Budget(seconds)
    cancelled = threading.Event()

    def watch() -> None:
        while not cancelled.wait(every_s):
            if budget.expired():
                on_timeout()
                return

    watching = threading.Thread(target=watch, name="run-time timeout", daemon=True)
    watching.start()

    def cancel() -> None:
        cancelled.set()
        watching.join()

    return cancel


_clock: Clock = WALL


def keep_time_by(clock: Clock) -> None:
    global _clock
    _clock = clock


def now() -> float:
    return _clock.seconds()


class Budget:
    def __init__(self, seconds: float) -> None:
        self._ends_at = now() + seconds

    def left(self) -> float:
        return max(0.0, self._ends_at - now())

    def expired(self) -> bool:
        return now() >= self._ends_at
