from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import NamedTuple

from fun_time.win32_process import get_process_creation_time, is_process_alive, list_child_pids

A_STARVED_CHILDS_START_S = 120.0


class StartedProcess(NamedTuple):
    pid: int
    started_at: int | None


def pid_written_to(path: Path, *, by: subprocess.Popen | None = None) -> int:
    deadline = time.monotonic() + A_STARVED_CHILDS_START_S
    while not (path.exists() and path.read_text()):
        if by is not None and by.poll() is not None:
            raise ChildProcessError(f"{path} was never written: its writer exited {by.returncode}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"nothing wrote a pid to {path} within {A_STARVED_CHILDS_START_S:g}s")
        time.sleep(0.05)
    return int(path.read_text())


def with_what_it_started(pid: int) -> list[StartedProcess]:
    """A venv's python starts the real interpreter a moment after it starts."""
    deadline = time.monotonic() + 5.0
    while not list_child_pids(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return [StartedProcess(each, get_process_creation_time(each))
            for each in (pid, *list_child_pids(pid))]


def running(process: StartedProcess) -> bool:
    """Its pid alive and still its own: a pid handed on is another process."""
    return (is_process_alive(process.pid)
            and get_process_creation_time(process.pid) == process.started_at)


def all_gone_within(processes: list[StartedProcess], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while any(map(running, processes)):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True
