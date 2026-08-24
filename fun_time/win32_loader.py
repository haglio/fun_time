"""Binding the Win32 DLLs, and saying plainly where they cannot be bound.

The Win32 wrappers here reach ``ctypes.windll`` while they are being imported:
the DLL handles and their argtypes are declared once, at module scope, so the
call sites stay bare.  That makes the binding a single point of failure for far
more than the code that needs Windows — off Windows ``ctypes`` has no ``windll``
at all, the import raises, and anything that names one of these modules
(``tests/conftest.py`` among them) goes with it.  One import then decides
whether a suite exists.

So the binding asks first.  ``WIN32_AVAILABLE`` says whether this interpreter's
``ctypes`` carries the Windows half — true on Windows, and true in a test
process that has faked that surface in.  Where it does not, ``load_dll`` hands
back a stand-in that takes argtypes like the real handle and raises
:class:`Win32Unavailable`, naming the entry point, the moment anything calls it.
Nothing degrades quietly: a call that should never have been reached says so
rather than returning a plausible zero.
"""
from __future__ import annotations

import ctypes
from typing import Any

WIN32_AVAILABLE = hasattr(ctypes, "windll")


class Win32Unavailable(RuntimeError):
    """A Win32 entry point was called in a process that could not bind it."""


class _UnavailableEntryPoint:
    """One exported function, on a machine that has no such export.

    Takes the ``argtypes``/``restype`` the modules declare at import the way any
    object takes an attribute, so that declaration survives as the import does.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise Win32Unavailable(f"{self._name} needs a Windows ctypes; this process has none")


class _UnavailableDll:
    """One DLL, on a machine that has no such DLL.

    Each entry point is made once and kept, so an ``argtypes`` declared on it
    is still there at the call site, and so a test that patches an entry point
    finds the same object the code will call.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, entry_point: str) -> _UnavailableEntryPoint:
        if entry_point.startswith("__"):
            raise AttributeError(entry_point)
        stand_in = _UnavailableEntryPoint(f"{self._name}.{entry_point}")
        setattr(self, entry_point, stand_in)
        return stand_in


class _UnavailableCallbackType:
    """A callback prototype, on a machine with no stdcall calling convention."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise Win32Unavailable("a Win32 callback prototype needs a Windows ctypes; this process has none")


def load_dll(name: str, *, use_last_error: bool = False) -> Any:
    """The handle for the *name* DLL, or a stand-in that refuses to be called.

    *use_last_error* asks ctypes to save and restore the per-thread error code
    around each call into this handle, which is the only way ``GetLastError``
    survives to be read from Python.  It also means a handle of this module's
    own rather than the process-wide one ``ctypes.windll`` caches — so the
    saving stays with the caller that asked for it.
    """
    if not WIN32_AVAILABLE:
        return _UnavailableDll(name)
    if use_last_error:
        return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]
    return getattr(ctypes.windll, name)  # type: ignore[attr-defined]


def win_functype(restype: Any, *argtypes: Any) -> Any:
    """The prototype for a stdcall callback, or a type that refuses to be made."""
    if not WIN32_AVAILABLE:
        return _UnavailableCallbackType
    return ctypes.WINFUNCTYPE(restype, *argtypes)  # type: ignore[attr-defined]


def get_last_error() -> int:
    """The error code ctypes saved around the last call into a guarded handle."""
    if not WIN32_AVAILABLE:
        raise Win32Unavailable("ctypes.get_last_error needs a Windows ctypes; this process has none")
    return ctypes.get_last_error()  # type: ignore[attr-defined]
