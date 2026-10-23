from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from fun_time.win32_process import get_process_creation_time, is_process_alive, list_child_pids
from tests.integration.run_clock import Budget

A_STARVED_CHILDS_START_S = 120.0

THE_INTERPRETER_NOT_A_VENVS_LAUNCHER = sys._base_executable


class StartedProcess(NamedTuple):
    pid: int
    started_at: int | None

    @classmethod
    def of(cls, pid: int) -> StartedProcess:
        return cls(pid, get_process_creation_time(pid))


@contextmanager
def an_interpreter_asleep(*args: str, for_s: float = 60.0) -> Iterator[subprocess.Popen]:
    child = subprocess.Popen(
        [THE_INTERPRETER_NOT_A_VENVS_LAUNCHER, "-c", f"import time; time.sleep({for_s})", *args])
    try:
        yield child
    finally:
        child.kill()
        child.wait()


def pid_written_to(path: Path, *, by: subprocess.Popen | None = None) -> int:
    budget = Budget(A_STARVED_CHILDS_START_S)
    while not (path.exists() and path.read_text()):
        if by is not None and by.poll() is not None:
            raise ChildProcessError(f"{path} was never written: its writer exited {by.returncode}")
        if budget.expired():
            raise TimeoutError(f"nothing wrote a pid to {path} within {A_STARVED_CHILDS_START_S:g}s")
        time.sleep(0.05)
    return int(path.read_text())


def with_what_it_started(pid: int) -> list[StartedProcess]:
    """A venv's python starts the real interpreter a moment after it starts."""
    budget = Budget(5.0)
    while not list_child_pids(pid) and not budget.expired():
        time.sleep(0.1)
    parent = StartedProcess.of(pid)
    children = [StartedProcess.of(each) for each in list_child_pids(pid)]
    return [parent, *(child for child in children if _born_after(child, parent))]


def _born_after(child: StartedProcess, parent: StartedProcess) -> bool:
    return None in (child.started_at, parent.started_at) or child.started_at >= parent.started_at


def running(process: StartedProcess) -> bool:
    """Its pid alive and still its own: a pid handed on is another process."""
    return (is_process_alive(process.pid)
            and get_process_creation_time(process.pid) == process.started_at)


def all_gone_within(processes: list[StartedProcess], seconds: float) -> bool:
    budget = Budget(seconds)
    while any(map(running, processes)):
        if budget.expired():
            return False
        time.sleep(0.1)
    return True
