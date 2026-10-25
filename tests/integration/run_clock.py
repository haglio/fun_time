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

from app_support.win32 import busy_seconds_of_the_job_this_process_runs_in

from fun_time.win32_loader import load_dll

_kernel32 = load_dll("kernel32", use_last_error=True)
_kernel32.GetSystemTimes.argtypes = [ctypes.POINTER(wt.FILETIME)] * 3
_kernel32.GetSystemTimes.restype = wt.BOOL


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
                ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", wt.LONG),
                ("tpDeltaPri", wt.LONG), ("dwFlags", wt.DWORD)]


class _SystemThreadInformation(ctypes.Structure):
    _fields_ = [("KernelTime", ctypes.c_int64), ("UserTime", ctypes.c_int64),
                ("CreateTime", ctypes.c_int64), ("WaitTime", wt.ULONG),
                ("StartAddress", ctypes.c_void_p), ("UniqueProcess", ctypes.c_void_p),
                ("UniqueThread", ctypes.c_void_p), ("Priority", wt.LONG),
                ("BasePriority", wt.LONG), ("ContextSwitches", wt.ULONG),
                ("ThreadState", wt.ULONG), ("WaitReason", wt.ULONG)]


_ntdll = load_dll("ntdll")
_ntdll.NtQueryInformationThread.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.ULONG, ctypes.POINTER(wt.ULONG)]
_ntdll.NtQueryInformationThread.restype = ctypes.c_long
_kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_kernel32.Thread32First.argtypes = [wt.HANDLE, ctypes.POINTER(_ThreadEntry)]
_kernel32.Thread32First.restype = wt.BOOL
_kernel32.Thread32Next.argtypes = [wt.HANDLE, ctypes.POINTER(_ThreadEntry)]
_kernel32.Thread32Next.restype = wt.BOOL
_kernel32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.OpenThread.restype = wt.HANDLE
_kernel32.CloseHandle.argtypes = [wt.HANDLE]

_TH32CS_SNAPTHREAD = 0x4
_THREAD_QUERY_INFORMATION = 0x0040
_THREAD_SYSTEM_THREAD_INFORMATION = 40
_READY, _DEFERRED_READY = 1, 7
_WAITING_FOR_A_PROCESSOR = frozenset({_READY, _DEFERRED_READY})


def _seconds(filetime: wt.FILETIME) -> float:
    return ((filetime.dwHighDateTime << 32) | filetime.dwLowDateTime) / 10_000_000


def machine_busy_seconds() -> float:
    idle, kernel, user = wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    _kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return _seconds(kernel) - _seconds(idle) + _seconds(user)


class Clock(Protocol):
    def seconds(self) -> float: ...


class _Wall:
    def seconds(self) -> float:
        return time.monotonic()


WALL = _Wall()


class RunClock:
    def __init__(self, *, run_busy: Callable[[], float] = busy_seconds_of_the_job_this_process_runs_in,
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


class _ThisProcessesThreads:
    def __init__(self) -> None:
        self._handles = {
            thread: handle for thread in _thread_ids_of(os.getpid())
            if (handle := _kernel32.OpenThread(_THREAD_QUERY_INFORMATION, False, thread))}

    def waiting_for_a_processor(self) -> set[int]:
        return {thread for thread, handle in self._handles.items()
                if _scheduler_state(handle) in _WAITING_FOR_A_PROCESSOR}

    def close(self) -> None:
        for handle in self._handles.values():
            _kernel32.CloseHandle(handle)


def _scheduler_state(handle: int) -> int | None:
    read = _SystemThreadInformation()
    if _ntdll.NtQueryInformationThread(handle, _THREAD_SYSTEM_THREAD_INFORMATION,
                                       ctypes.byref(read), ctypes.sizeof(read), None):
        return None
    return read.ThreadState


def _thread_ids_of(process: int) -> list[int]:
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    entry = _ThreadEntry(dwSize=ctypes.sizeof(_ThreadEntry))
    found = []
    try:
        more = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == process:
                found.append(entry.th32ThreadID)
            more = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return found


class OnTimeClock:
    def __init__(self, *, slack_s: float, nap_s: float = 0.005,
                 nap: Callable[[float], None] = time.sleep,
                 wall: Callable[[], float] = time.perf_counter,
                 waiting: Callable[[], set[int]] | None = None) -> None:
        self._seconds = 0.0
        self._held = False
        self._stopped = threading.Event()
        self._threads = _ThisProcessesThreads() if waiting is None else None
        self._thread = threading.Thread(
            target=self._keep_time,
            args=(nap_s, slack_s, nap, wall, waiting or self._threads.waiting_for_a_processor),
            name="on-time clock", daemon=True)
        self._thread.start()

    def _keep_time(self, nap_s: float, slack_s: float, nap: Callable[[float], None],
                   wall: Callable[[], float], waiting: Callable[[], set[int]]) -> None:
        last = wall()
        since: dict[int, float] = {}
        while not self._stopped.is_set():
            nap(nap_s)
            now = wall()
            since = {thread: since.get(thread, now) for thread in waiting()}
            self._held = (now - last > nap_s + slack_s
                          or any(now - began > slack_s for began in since.values()))
            if not self._held:
                self._seconds += now - last
            last = now

    def seconds(self) -> float:
        return self._seconds

    def held(self) -> bool:
        return self._held

    def __enter__(self) -> OnTimeClock:
        return self

    def __exit__(self, *_exc) -> None:
        self._stopped.set()
        self._thread.join()
        if self._threads is not None:
            self._threads.close()


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
