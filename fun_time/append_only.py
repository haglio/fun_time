from __future__ import annotations

import ctypes
import ctypes.wintypes
from pathlib import Path

from fun_time.win32_loader import get_last_error, load_dll

_kernel32 = load_dll("kernel32", use_last_error=True)

FILE_APPEND_DATA = 0x0004
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ_WRITE_DELETE = 0x0007
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

_kernel32.CreateFileW.argtypes = [
    ctypes.wintypes.LPCWSTR,  # lpFileName
    ctypes.wintypes.DWORD,    # dwDesiredAccess
    ctypes.wintypes.DWORD,    # dwShareMode
    ctypes.wintypes.LPVOID,   # lpSecurityAttributes
    ctypes.wintypes.DWORD,    # dwCreationDisposition
    ctypes.wintypes.DWORD,    # dwFlagsAndAttributes
    ctypes.wintypes.HANDLE,   # hTemplateFile
]
_kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
_kernel32.WriteFile.argtypes = [
    ctypes.wintypes.HANDLE,                  # hFile
    ctypes.wintypes.LPCVOID,                 # lpBuffer
    ctypes.wintypes.DWORD,                   # nNumberOfBytesToWrite
    ctypes.POINTER(ctypes.wintypes.DWORD),   # lpNumberOfBytesWritten
    ctypes.wintypes.LPVOID,                  # lpOverlapped
]
_kernel32.WriteFile.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL


def append_line(path: Path, text: str) -> None:
    handle = _kernel32.CreateFileW(
        str(path), FILE_APPEND_DATA | SYNCHRONIZE, FILE_SHARE_READ_WRITE_DELETE,
        None, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(get_last_error())
    try:
        data = text.encode("utf-8")
        written = ctypes.wintypes.DWORD()
        if not _kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None):
            raise ctypes.WinError(get_last_error())
    finally:
        _kernel32.CloseHandle(handle)
