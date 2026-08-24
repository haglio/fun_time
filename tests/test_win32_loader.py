"""What a Win32 wrapper does on a machine that has no Win32.

Every wrapper in this package binds its DLLs while it is being imported, so an
interpreter whose ``ctypes`` carries no ``windll`` cannot import them — and a
``conftest`` that names one takes the whole run down with it rather than losing
the tests that actually need Windows.  These cases pin the other outcome: the
modules import, they say plainly that they bound nothing, and a call that
reaches a stand-in names itself instead of returning a plausible zero.

The child interpreters below delete the Windows half of ``ctypes`` before
importing anything, so each case asks the same question on Windows as it does
anywhere else.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fun_time import win32_loader

REPO_DIR = Path(__file__).resolve().parent.parent

# The names ``ctypes`` grows only on Windows, and that the modules under test
# reach for while they are being imported.
_WIN32_CTYPES_NAMES = (
    "windll", "oledll", "WinDLL", "OleDLL", "WINFUNCTYPE", "HRESULT",
    "get_last_error", "set_last_error",
)

_STRIP_WIN32_FROM_CTYPES = f"""
import ctypes, ctypes.wintypes
for _name in {_WIN32_CTYPES_NAMES!r}:
    if hasattr(ctypes, _name):
        delattr(ctypes, _name)
"""


def _run_without_the_win32_ctypes_surface(body: str) -> subprocess.CompletedProcess:
    """Run *body* in a child whose ``ctypes`` has had its Windows half removed.

    ``PYTHONPATH`` is dropped so the child cannot pick up a shim that fakes that
    surface back in, the way a run on a developer's non-Windows machine does.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [sys.executable, "-c", _STRIP_WIN32_FROM_CTYPES + body],
        cwd=str(REPO_DIR), env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.fixture()
def unavailable(monkeypatch):
    """Answer as an interpreter whose ctypes carries no Windows half would."""
    monkeypatch.setattr(win32_loader, "WIN32_AVAILABLE", False)


def test_the_win32_wrapper_imports_where_ctypes_has_no_windll():
    result = _run_without_the_win32_ctypes_surface(
        "import fun_time.win32\n"
        "from fun_time.win32_loader import WIN32_AVAILABLE\n"
        "assert WIN32_AVAILABLE is False, WIN32_AVAILABLE\n"
    )
    assert result.returncode == 0, result.stderr


def test_the_flag_says_whether_this_ctypes_can_bind_a_dll():
    assert win32_loader.WIN32_AVAILABLE is hasattr(ctypes, "windll")


def test_a_call_that_reaches_an_unbound_entry_point_names_it(unavailable):
    """The stand-in must never pass for a call that worked."""
    user32 = win32_loader.load_dll("user32")

    with pytest.raises(win32_loader.Win32Unavailable, match=r"user32\.SetWindowPos"):
        user32.SetWindowPos(0, 0, 0, 0, 0, 0, 0)


def test_an_unbound_entry_point_still_takes_the_argtypes_declared_on_it(unavailable):
    """The wrappers declare argtypes at import; that has to survive too."""
    kernel32 = win32_loader.load_dll("kernel32")

    kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE

    assert kernel32.OpenProcess.argtypes == [ctypes.wintypes.DWORD]
    assert kernel32.OpenProcess.restype is ctypes.wintypes.HANDLE


def test_an_unbound_entry_point_is_the_same_object_every_time(unavailable):
    """A test that patches one has to be patching what the code will call."""
    user32 = win32_loader.load_dll("user32")

    assert user32.ShowWindow is user32.ShowWindow


def test_an_unbound_callback_prototype_refuses_to_be_made(unavailable):
    prototype = win32_loader.win_functype(ctypes.wintypes.BOOL, ctypes.wintypes.HWND)

    with pytest.raises(win32_loader.Win32Unavailable):
        prototype(lambda hwnd: True)
