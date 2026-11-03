from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

_READ_AND_WRITE, _SHARED_WITH_NOBODY, _OPEN_ALWAYS, _NORMAL = 0xC0000000, 0, 4, 0x80


@contextmanager
def held_by_another_program(path: Path) -> Iterator[Callable[[], None]]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), _READ_AND_WRITE, _SHARED_WITH_NOBODY, None, _OPEN_ALWAYS,
                                  _NORMAL, None)
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    still_open = [handle]

    def let_go() -> None:
        while still_open:
            kernel32.CloseHandle(still_open.pop())

    try:
        yield let_go
    finally:
        let_go()


def let_go_soon(let_go: Callable[[], None]) -> None:
    threading.Timer(0.1, let_go).start()
