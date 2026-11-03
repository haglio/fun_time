from __future__ import annotations

import subprocess
import sys

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
