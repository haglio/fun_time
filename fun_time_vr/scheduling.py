from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from app_support.win32_loader import get_last_error, load_dll
from funestra.funestra_priority import (
    D3DKMT_SCHEDULINGPRIORITYCLASS_REALTIME,
    ahead_on_the_processor,
)

logger = logging.getLogger(__name__)


def _avrt():
    return load_dll("avrt", use_last_error=True)


def _gdi32():
    return load_dll("gdi32", use_last_error=True)


def _declared_avrt(dll):
    dll.AvSetMmThreadCharacteristicsW.argtypes = [
        ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
    dll.AvSetMmThreadCharacteristicsW.restype = ctypes.wintypes.HANDLE
    dll.AvRevertMmThreadCharacteristics.argtypes = [ctypes.wintypes.HANDLE]
    dll.AvRevertMmThreadCharacteristics.restype = ctypes.wintypes.BOOL
    return dll


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
def ahead_of_background_work(*, avrt=_avrt, gdi32=_gdi32, kernel32=None,
                             last_error=get_last_error) -> Iterator[None]:
    with (ahead_on_the_processor(kernel32, last_error, gdi32=gdi32(),
                                 graphics_card_class=D3DKMT_SCHEDULINGPRIORITYCLASS_REALTIME),
          scheduled_as_a_game(avrt=avrt, last_error=last_error)):
        yield
