from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fun_time.win32_loader import get_last_error, load_dll

logger = logging.getLogger(__name__)

_ABOVE_NORMAL_PRIORITY_CLASS = 0x8000
_THIS_PROCESS = ctypes.wintypes.HANDLE(-1)

_kernel32 = load_dll("kernel32", use_last_error=True)
_kernel32.GetPriorityClass.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.GetPriorityClass.restype = ctypes.wintypes.DWORD
_kernel32.SetPriorityClass.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
_kernel32.SetPriorityClass.restype = ctypes.wintypes.BOOL


@contextmanager
def ahead_on_the_processor(kernel32=None, last_error=get_last_error) -> Iterator[None]:
    kernel32 = _kernel32 if kernel32 is None else kernel32
    found = kernel32.GetPriorityClass(_THIS_PROCESS)
    raised = kernel32.SetPriorityClass(_THIS_PROCESS, _ABOVE_NORMAL_PRIORITY_CLASS)
    if not raised:
        logger.warning(
            "Windows would not put this Funestra ahead of background work (error %d), so a "
            "busy computer can make its pictures and the OSR2's motion uneven", last_error())
    try:
        yield
    finally:
        if raised:
            kernel32.SetPriorityClass(_THIS_PROCESS, found)
