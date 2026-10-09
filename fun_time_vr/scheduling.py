from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fun_time.win32_loader import get_last_error, load_dll

logger = logging.getLogger(__name__)

D3DKMT_SCHEDULINGPRIORITYCLASS_REALTIME = 5
ABOVE_NORMAL_PRIORITY_CLASS = 0x8000


def _avrt():
    return load_dll("avrt", use_last_error=True)


def _gdi32():
    return load_dll("gdi32", use_last_error=True)


def _kernel32():
    return load_dll("kernel32", use_last_error=True)


def _this_process():
    kernel32 = load_dll("kernel32")
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    return kernel32.GetCurrentProcess()


def _declared_avrt(dll):
    dll.AvSetMmThreadCharacteristicsW.argtypes = [
        ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
    dll.AvSetMmThreadCharacteristicsW.restype = ctypes.wintypes.HANDLE
    dll.AvRevertMmThreadCharacteristics.argtypes = [ctypes.wintypes.HANDLE]
    dll.AvRevertMmThreadCharacteristics.restype = ctypes.wintypes.BOOL
    return dll


def _declared_gdi32(dll):
    dll.D3DKMTGetProcessSchedulingPriorityClass.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
    dll.D3DKMTGetProcessSchedulingPriorityClass.restype = ctypes.c_long
    dll.D3DKMTSetProcessSchedulingPriorityClass.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int]
    dll.D3DKMTSetProcessSchedulingPriorityClass.restype = ctypes.c_long
    return dll


def _declared_kernel32(dll):
    dll.GetPriorityClass.argtypes = [ctypes.wintypes.HANDLE]
    dll.GetPriorityClass.restype = ctypes.wintypes.DWORD
    dll.SetPriorityClass.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    dll.SetPriorityClass.restype = ctypes.wintypes.BOOL
    return dll


@contextmanager
def _ahead_on_the_processor(kernel32, process, last_error) -> Iterator[None]:
    found = kernel32.GetPriorityClass(process)
    raised = kernel32.SetPriorityClass(process, ABOVE_NORMAL_PRIORITY_CLASS)
    if not raised:
        logger.warning(
            "Windows would not put this process ahead of background work (error %d), so a "
            "busy machine can starve the threads its pictures and its frames wait on",
            last_error())
    try:
        yield
    finally:
        if raised:
            kernel32.SetPriorityClass(process, found)


@contextmanager
def _first_on_the_graphics_card(gdi, process) -> Iterator[None]:
    found = ctypes.c_int()
    gdi.D3DKMTGetProcessSchedulingPriorityClass(process, ctypes.byref(found))
    status = gdi.D3DKMTSetProcessSchedulingPriorityClass(
        process, D3DKMT_SCHEDULINGPRIORITYCLASS_REALTIME)
    if status:
        logger.warning(
            "Windows would not put this process first on the graphics card (status 0x%08x), "
            "so a busy graphics card can hold its frames up", status & 0xFFFFFFFF)
    try:
        yield
    finally:
        if not status:
            gdi.D3DKMTSetProcessSchedulingPriorityClass(process, found.value)


@contextmanager
def scheduled_as_a_game(*, avrt=_avrt, last_error=get_last_error) -> Iterator[None]:
    with _scheduled_as_a_game(_declared_avrt(avrt()), last_error):
        yield


@contextmanager
def _scheduled_as_a_game(avrt, last_error) -> Iterator[None]:
    task_index = ctypes.wintypes.DWORD(0)
    registration = avrt.AvSetMmThreadCharacteristicsW("Games", ctypes.byref(task_index))
    if not registration:
        logger.warning(
            "Windows would not schedule this thread as a game (error %d), so it keeps "
            "normal priority and a busy machine can hold it up", last_error())
    try:
        yield
    finally:
        if registration:
            avrt.AvRevertMmThreadCharacteristics(registration)


@contextmanager
def ahead_of_background_work(*, avrt=_avrt, gdi32=_gdi32, kernel32=_kernel32,
                             this_process=_this_process,
                             last_error=get_last_error) -> Iterator[None]:
    process = this_process()
    with (_ahead_on_the_processor(_declared_kernel32(kernel32()), process, last_error),
          _first_on_the_graphics_card(_declared_gdi32(gdi32()), process),
          scheduled_as_a_game(avrt=avrt, last_error=last_error)):
        yield
