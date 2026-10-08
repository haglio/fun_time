from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.process_tree import kill_process_tree
from tests.child_reports import all_gone_within, pid_written_to, with_what_it_started

ROOT = Path(__file__).resolve().parent.parent

_STARTS_ONE_OF_ITS_OWN = """
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         creationflags=subprocess.CREATE_NO_WINDOW)
pathlib.Path({report!r}).write_text(str(child.pid))
time.sleep(60)
"""


@dataclass
class _Process:
    parent: int
    created: int


class FakeWindows:
    """A process table the way Windows shows it: a pid, its parent's pid and
    when it was created, with a handle that keeps a pid from being handed on."""

    def __init__(self, processes: dict[int, tuple[int, int]]):
        self.running = {pid: _Process(*each) for pid, each in processes.items()}
        self.when_ended: dict[int, callable] = {}
        self.open_handles: set[int] = set()
        self.asked_for: list[int] = []

    def start(self, pid: int, *, parent: int, created: int) -> None:
        self.running[pid] = _Process(parent, created)

    def parents(self) -> list[tuple[int, int]]:
        return [(pid, each.parent) for pid, each in self.running.items()]

    def open(self, pid: int) -> int | None:
        self.asked_for.append(pid)
        if pid not in self.running:
            return None
        self.open_handles.add(pid)
        return pid

    def created(self, handle: int) -> int:
        return self.running[handle].created

    def terminate(self, handle: int) -> None:
        if self.running.pop(handle, None) is not None:
            self.when_ended.pop(handle, lambda: None)()

    def wait(self, handle: int, timeout_s: float) -> None:
        pass

    def close(self, handle: int) -> None:
        self.open_handles.discard(handle)


def test_what_a_process_starts_while_it_is_being_ended_is_ended_too():
    windows = FakeWindows({10: (1, 100), 11: (10, 101)})

    def the_interpreter_starts_a_decoder_as_its_launcher_goes():
        windows.start(12, parent=11, created=102)
        windows.terminate(11)

    windows.when_ended[10] = the_interpreter_starts_a_decoder_as_its_launcher_goes

    kill_process_tree(10, windows=windows)

    assert windows.running == {}


def test_a_process_listed_under_a_pid_from_before_that_pid_was_handed_on_is_spared():
    windows = FakeWindows({10: (1, 100), 30: (10, 50)})

    kill_process_tree(10, windows=windows)

    assert list(windows.running) == [30]


def test_every_handle_it_opens_is_closed():
    windows = FakeWindows({10: (1, 100), 11: (10, 101), 30: (10, 50)})

    kill_process_tree(10, windows=windows)

    assert windows.open_handles == set()


def test_the_zero_pid_of_a_child_that_was_never_launched_is_left_alone():
    windows = FakeWindows({})

    kill_process_tree(0, windows=windows)

    assert windows.asked_for == []


def test_ending_a_tree_starts_no_console_program_of_its_own():
    """A session the crossing relay started runs under the windowed interpreter,
    so a console program it started would get a window of its own, on screen:
    quitting once flashed a row of black windows that way."""
    windows = FakeWindows({10: (1, 100), 11: (10, 101)})

    with patch("subprocess.Popen", side_effect=AssertionError("started a program")):
        kill_process_tree(10, windows=windows)

    assert windows.running == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 processes")
def test_a_real_process_and_everything_it_started_end(tmp_path: Path):
    report = tmp_path / "child_pid.txt"
    raiser = subprocess.Popen(
        [sys.executable, "-c", _STARTS_ONE_OF_ITS_OWN.format(report=str(report))],
        cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        everything = [*with_what_it_started(raiser.pid),
                      *with_what_it_started(pid_written_to(report, by=raiser))]

        kill_process_tree(raiser.pid)

        assert all_gone_within(everything, 10.0)
    finally:
        if raiser.poll() is None:
            raiser.kill()
            raiser.wait()
