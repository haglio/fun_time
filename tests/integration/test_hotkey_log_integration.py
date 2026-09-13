"""The hotkey script and the orchestrator both append to the bridge log.

A run polls that log for the line a session start waits on.  The script's
FileAppend took the file exclusively, and a line that met another handle was
retried three times and dropped; shared, the two writers' appends landed on each
other's offsets instead.  Either way a start that lost "Session up" timed out
with its session fully up.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.win32_loader import get_last_error, load_dll
from fun_time.windows_bridge_orchestrator import _AppendOnWriteHandler
from tests.ahk_script import function_source

from .hidden_desktop import run_where_nothing_is_focused
from .integration_support import real_config_path

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="AutoHotkey is the Windows side of the bridge",
)

LINE = "Session up; startup hold released"
SCRIPT_LINES = 2000

_kernel32 = load_dll("kernel32", use_last_error=True)
_kernel32.CreateFileW.argtypes = [
    ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.LPVOID,
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.HANDLE,
]
_kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_ERROR_SHARING_VIOLATION = 32


def _logging_script(tmp_path: Path, body: str) -> Path:
    """A script that has the real Log() and runs *body* with it."""
    script = tmp_path / "log.ahk"
    script.write_text(
        "#Requires AutoHotkey v2.0\n"
        "#NoTrayIcon\n\n"
        "WINDOWS_BRIDGE_LOG_FILE := A_Args[1]\n\n"
        f"{function_source('AppendWithRetry')}\n\n"
        f"{function_source('Log')}\n\n"
        f"{body}\n"
        "ExitApp\n",
        encoding="utf-8",
    )
    return script


def _ahk_exe() -> str:
    return str(load_config(real_config_path()).paths.ahk_exe)


@pytest.mark.parametrize("mode", ["r", "a"], ids=["while_polled", "while_appended_to"])
def test_a_logged_line_lands_while_the_log_is_open_elsewhere(tmp_path: Path, mode: str):
    log = tmp_path / "windows_bridge.log"
    log.touch()
    script = _logging_script(tmp_path, f'Log("{LINE}")')

    with log.open(mode, encoding="utf-8"):
        exit_code = run_where_nothing_is_focused([_ahk_exe(), str(script), str(log)])

    assert exit_code == 0, "the hotkey script did not run to completion"
    assert LINE in log.read_text(encoding="utf-8"), "the line was dropped"


def test_neither_writer_loses_a_line_to_the_other(tmp_path: Path):
    log = tmp_path / "windows_bridge.log"
    script = _logging_script(
        tmp_path, f'loop {SCRIPT_LINES} {{\n    Log("hotkey line " . A_Index)\n}}')
    orchestrator = logging.getLogger(f"{__name__}.orchestrator")
    orchestrator.propagate = False
    orchestrator.setLevel(logging.INFO)
    handler = _AppendOnWriteHandler(log)
    orchestrator.addHandler(handler)
    finished = threading.Event()
    sent = 0

    def append_until_the_script_is_done():
        nonlocal sent
        while not finished.is_set():
            sent += 1
            orchestrator.info("orchestrator line %d", sent)

    writer = threading.Thread(target=append_until_the_script_is_done)
    writer.start()
    try:
        exit_code = run_where_nothing_is_focused(
            [_ahk_exe(), str(script), str(log)], timeout_seconds=120)
    finally:
        finished.set()
        writer.join()
        orchestrator.removeHandler(handler)

    text = log.read_text(encoding="utf-8")
    script_landed = {int(n) for n in re.findall(r"hotkey line (\d+)\n", text)}
    orchestrator_landed = {int(n) for n in re.findall(r"orchestrator line (\d+)\n", text)}

    assert exit_code == 0, "the hotkey script did not run to completion"
    assert SCRIPT_LINES - len(script_landed) == 0, "the hotkey script lost lines to the orchestrator"
    assert sent - len(orchestrator_landed) == 0, "the orchestrator lost lines to the hotkey script"


def _within(seconds: float, condition) -> bool:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _no_one_else_has_it_open(path: Path) -> bool:
    handle = _kernel32.CreateFileW(str(path), _GENERIC_READ, 0, None, _OPEN_EXISTING, 0, None)
    if handle == ctypes.wintypes.HANDLE(-1).value:
        assert get_last_error() == _ERROR_SHARING_VIOLATION, f"{path} could not be opened at all"
        return False
    _kernel32.CloseHandle(handle)
    return True


def test_a_logged_line_leaves_no_handle_on_the_log_behind(tmp_path: Path):
    log = tmp_path / "windows_bridge.log"
    logged, finished = tmp_path / "logged.flag", tmp_path / "finished.flag"
    script = _logging_script(
        tmp_path,
        f'loop 3\n    Log("{LINE}")\n'
        'FileAppend("", A_Args[2])\n'
        "deadline := A_TickCount + 120000\n"
        "while !FileExist(A_Args[3]) && A_TickCount < deadline\n"
        "    Sleep(50)",
    )
    hotkey_script = subprocess.Popen([_ahk_exe(), str(script), str(log), str(logged), str(finished)])
    try:
        assert _within(60, logged.exists), "the hotkey script never finished logging"
        closed = _within(5, lambda: _no_one_else_has_it_open(log))
    finally:
        finished.touch()
        try:
            hotkey_script.wait(timeout=10)
        except subprocess.TimeoutExpired:
            hotkey_script.kill()
            hotkey_script.wait()

    assert closed, "the hotkey script, still running, holds the log open after its last line"
