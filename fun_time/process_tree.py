"""Ending a process with everything it started, including what it starts while it goes."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import time
from typing import NamedTuple

from fun_time.win32_loader import load_dll
from fun_time.win32_process import creation_time_of, process_parents

_kernel32 = load_dll("kernel32")

_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000

_kernel32.OpenProcess.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.TerminateProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT]
_kernel32.TerminateProcess.restype = ctypes.wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

ENDING_BUDGET_S = 10.0


class _Windows:
    parents = staticmethod(process_parents)
    created = staticmethod(creation_time_of)

    @staticmethod
    def open(pid: int) -> int | None:
        return _kernel32.OpenProcess(
            _PROCESS_TERMINATE | _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)

    @staticmethod
    def terminate(handle: int) -> None:
        _kernel32.TerminateProcess(handle, 1)

    @staticmethod
    def wait(handle: int, timeout_s: float) -> None:
        _kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))

    @staticmethod
    def close(handle: int) -> None:
        _kernel32.CloseHandle(handle)


class _Held(NamedTuple):
    handle: int
    created: int


def kill_process_tree(pid: int, *, windows=_Windows) -> None:
    """Kill *pid* and its descendants, and whatever they start while they go.

    Unconditional.  A bare PID is evidence of nothing — Windows hands freed PIDs
    straight back out — so the caller must first establish that *pid* is theirs
    to kill: kill_recorded_child() checks the recorded creation time, and the
    integration reap checks the image name of a window it found on its own
    desktop.
    """
    if not pid:
        return
    held: dict[int, _Held] = {}
    passed_over: set[int] = set()
    ended: set[int] = set()
    try:
        if not _hold(windows, held, pid, born_after=0):
            return
        while True:
            while _hold_their_children(windows, held, passed_over):
                pass
            going = [each for each in held if each not in ended]
            if not going:
                return
            for each in going:
                windows.terminate(held[each].handle)
            deadline = time.monotonic() + ENDING_BUDGET_S
            for each in going:
                windows.wait(held[each].handle, max(0.0, deadline - time.monotonic()))
            ended.update(going)
    finally:
        for each in held.values():
            windows.close(each.handle)


def _hold(windows, held: dict[int, _Held], pid: int, *, born_after: int) -> bool:
    handle = windows.open(pid)
    if not handle:
        return False
    created = windows.created(handle)
    if created is None or created < born_after:
        windows.close(handle)
        return False
    held[pid] = _Held(handle, created)
    return True


def _hold_their_children(windows, held: dict[int, _Held], passed_over: set[int]) -> bool:
    grew = False
    for pid, parent in windows.parents():
        if parent in held and pid not in held and pid not in passed_over:
            if _hold(windows, held, pid, born_after=held[parent].created):
                grew = True
            else:
                passed_over.add(pid)
    return grew
