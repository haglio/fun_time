"""Whether this process is off-screen, so a player the user cannot see is one he cannot hear."""
from __future__ import annotations

import ctypes

from fun_time.win32_loader import WIN32_AVAILABLE, load_dll

_INTERACTIVE_DESKTOP_NAME = "Default"
_UOI_NAME = 2
_DESKTOP_READOBJECTS = 0x0001

if WIN32_AVAILABLE:
    _user32 = load_dll("user32", use_last_error=True)
    _user32.GetThreadDesktop.restype = ctypes.c_void_p
    _user32.GetThreadDesktop.argtypes = [ctypes.c_ulong]
    _user32.OpenInputDesktop.restype = ctypes.c_void_p
    _user32.OpenInputDesktop.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    _user32.CloseDesktop.restype = ctypes.c_int
    _user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    _user32.GetUserObjectInformationW.restype = ctypes.c_int
    _user32.GetUserObjectInformationW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong)]
    _kernel32 = load_dll("kernel32", use_last_error=True)
    _kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
    _kernel32.GetCurrentThreadId.argtypes = []


def _name_of(handle: int | None) -> str | None:
    if not handle:
        return None
    needed = ctypes.c_ulong(0)
    _user32.GetUserObjectInformationW(handle, _UOI_NAME, None, 0, ctypes.byref(needed))
    if needed.value == 0:
        return None
    buffer = ctypes.create_unicode_buffer(needed.value // ctypes.sizeof(ctypes.c_wchar))
    if not _user32.GetUserObjectInformationW(
            handle, _UOI_NAME, buffer, needed.value, ctypes.byref(needed)):
        return None
    return buffer.value


def current_desktop_name() -> str | None:
    """This thread's desktop; its handle is the system's, so it is read, never closed."""
    if not WIN32_AVAILABLE:
        return None
    return _name_of(_user32.GetThreadDesktop(_kernel32.GetCurrentThreadId()))


def input_desktop_name() -> str | None:
    """The desktop the user's input goes to; None when it is a secure one we cannot open."""
    if not WIN32_AVAILABLE:
        return None
    handle = _user32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
    if not handle:
        return None
    try:
        return _name_of(handle)
    finally:
        _user32.CloseDesktop(handle)


def _is_off_screen(mine: str | None, active: str | None) -> bool:
    if mine is None:
        return False
    reference = active if active is not None else _INTERACTIVE_DESKTOP_NAME
    return mine.casefold() != reference.casefold()


def on_hidden_desktop() -> bool:
    return _is_off_screen(current_desktop_name(), input_desktop_name())
