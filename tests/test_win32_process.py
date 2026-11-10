from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from fun_time import win32_process
from fun_time.child_launch import no_console_window
from fun_time.win32_process import is_process_alive


def test_a_running_process_is_alive():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **no_console_window())
    try:
        assert is_process_alive(child.pid) is True
    finally:
        child.kill()
        child.wait()


def test_an_exited_process_whose_handle_is_still_held_is_not_alive():
    child = subprocess.Popen([sys.executable, "-c", "pass"], **no_console_window())
    child.wait()

    assert is_process_alive(child.pid) is False


def _windows_entry_point(node) -> tuple[str, str] | None:
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in ("_kernel32", "_ntdll")):
        return node.value.id, node.attr
    return None


def test_every_windows_call_declares_argtypes_against_64_bit_handle_truncation():
    source = ast.parse(Path(win32_process.__file__).read_text(encoding="utf-8"))
    called = {
        _windows_entry_point(node.func)
        for node in ast.walk(source) if isinstance(node, ast.Call)} - {None}
    declared = {
        _windows_entry_point(target.value)
        for node in ast.walk(source) if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute) and target.attr == "argtypes"}

    assert ("_kernel32", "CloseHandle") in called
    assert called - declared == set()
