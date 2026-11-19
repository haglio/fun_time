from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app_support.win32_loader import load_dll

_kernel32 = load_dll("kernel32", use_last_error=True)
_kernel32.CreateEventW.restype = ctypes.wintypes.HANDLE
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.BOOL,
                                   ctypes.wintypes.LPCWSTR]
_kernel32.OpenEventW.restype = ctypes.wintypes.HANDLE
_kernel32.OpenEventW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL,
                                 ctypes.wintypes.LPCWSTR]
_kernel32.SetEvent.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

EVENT_MODIFY_STATE = 0x0002
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
_LONGEST_NAME = 250


def bell_name(cmd_file: str | Path) -> str:
    return r"Local\FunTime_" + re.sub(r"[^a-z0-9]", "_", str(cmd_file).lower())


def ring(cmd_file: str | Path) -> bool:
    name = bell_name(cmd_file)
    if len(name) > _LONGEST_NAME:
        return False
    handle = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(_kernel32.SetEvent(handle))
    finally:
        _kernel32.CloseHandle(handle)


class Bell(Protocol):
    def wait(self, timeout: float | None) -> bool: ...

    def ring_here(self) -> None: ...

    def close(self) -> None: ...


class CommandBell:
    def __init__(self, cmd_file: str | Path) -> None:
        self._rung = threading.Event()
        self._closing = False
        name = bell_name(cmd_file)
        self._handle = (_kernel32.CreateEventW(None, False, False, name)
                        if len(name) <= _LONGEST_NAME else None)
        self._listener: threading.Thread | None = None
        if self._handle:
            self._listener = threading.Thread(target=self._listen, daemon=True,
                                              name="command-bell")
            self._listener.start()

    def wait(self, timeout: float | None) -> bool:
        rang = self._rung.wait(timeout)
        self._rung.clear()
        return rang

    def ring_here(self) -> None:
        self._rung.set()

    def close(self) -> None:
        if not self._handle:
            return
        self._closing = True
        _kernel32.SetEvent(self._handle)
        if self._listener is not None:
            self._listener.join()
        _kernel32.CloseHandle(self._handle)
        self._handle = None

    def _listen(self) -> None:
        while _kernel32.WaitForSingleObject(self._handle, INFINITE) == WAIT_OBJECT_0:
            if self._closing:
                return
            self._rung.set()


def wait_out_the_tick(*, bell: Bell, stop: threading.Event, clock: Callable[[], float],
                      answer: Callable[[], None], tick_s: float) -> None:
    deadline = clock() + tick_s
    while not stop.is_set():
        left = deadline - clock()
        if left <= 0 or not bell.wait(left):
            return
        answer()
