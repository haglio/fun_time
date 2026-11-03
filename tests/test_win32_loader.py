from __future__ import annotations

import ctypes
import ctypes.wintypes
import subprocess
import sys
from pathlib import Path

from fun_time import win32
from tests.integration.integration_support import environment_with_this_checkouts_siblings

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
from fun_time.project_paths import apply_sibling_checkouts_to_sys_path
apply_sibling_checkouts_to_sys_path()
"""


PACKAGE_MODULES = tuple(
    f"fun_time.{path.stem}"
    for path in sorted((REPO_DIR / "fun_time").glob("*.py"))
    if path.stem != "__init__"
)


def _run_without_the_win32_ctypes_surface(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _STRIP_WIN32_FROM_CTYPES + body],
        cwd=str(REPO_DIR), env=environment_with_this_checkouts_siblings(),
        capture_output=True, text=True, timeout=180, check=False,
    )


def test_the_win32_wrapper_imports_where_ctypes_has_no_windll():
    result = _run_without_the_win32_ctypes_surface(
        "import fun_time.win32\n"
        "from app_support.win32_loader import WIN32_AVAILABLE\n"
        "assert WIN32_AVAILABLE is False, WIN32_AVAILABLE\n"
    )
    assert result.returncode == 0, result.stderr


def test_the_package_modules_are_found():
    assert "fun_time.win32" in PACKAGE_MODULES
    assert "fun_time.single_instance" in PACKAGE_MODULES


def test_every_module_in_the_package_imports_where_ctypes_has_no_windll():
    result = _run_without_the_win32_ctypes_surface(
        "import importlib\n"
        "failures = []\n"
        f"for _name in {PACKAGE_MODULES!r}:\n"
        "    try:\n"
        "        importlib.import_module(_name)\n"
        "    except BaseException as exc:\n"
        "        failures.append(f'{_name}: {type(exc).__name__}: {exc}')\n"
        "print('\\n'.join(failures))\n"
        "raise SystemExit(1 if failures else 0)\n"
    )
    assert result.returncode == 0, result.stdout or result.stderr


def test_the_integration_support_modules_import_where_ctypes_has_no_windll():
    result = _run_without_the_win32_ctypes_surface(
        "import tests.integration.hidden_desktop\n"
        "import tests.integration.integration_support\n"
        "import tests.integration.session_lock\n"
    )
    assert result.returncode == 0, result.stderr


def test_our_own_rect_carries_win32s_own_memory_layout():
    theirs = ctypes.wintypes.RECT
    ours = win32.Win32Rect

    assert [name for name, _ in ours._fields_] == ["left", "top", "right", "lower"]
    assert ctypes.sizeof(ours) == ctypes.sizeof(theirs)
    assert (
        [(getattr(ours, name).offset, getattr(ours, name).size)
         for name, _ in ours._fields_]
        == [(getattr(theirs, name).offset, getattr(theirs, name).size)
            for name, _ in theirs._fields_]
    )
