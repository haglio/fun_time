"""Unit tests: an integration run's waits are counted in the seconds it could have run."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading

import pytest

from fun_time.win32_job import a_job_whose_processes_end_with_it
from tests.integration import run_clock
from tests.integration.run_clock import (
    Budget,
    RunClock,
    busy_seconds_of_job,
    keep_time_by,
    machine_busy_seconds,
    watch_for_a_timeout,
)


class _Machine:
    """Sixteen processors, and the busy seconds each kind of work has used on them."""

    def __init__(self) -> None:
        self.at = 1000.0
        self.others = 0.0
        self.run = 0.0

    def clock(self) -> RunClock:
        return RunClock(run_busy=lambda: self.run, machine_busy=lambda: self.others + self.run,
                        processors=16, wall=lambda: self.at)

    def spend(self, seconds: float, *, others_hold: float, run_holds: float = 0.0) -> None:
        self.at += seconds
        self.others += others_hold * seconds
        self.run += run_holds * seconds


class _Frozen:
    def __init__(self) -> None:
        self.at = 0.0

    def seconds(self) -> float:
        return self.at


@pytest.fixture
def frozen():
    clock = _Frozen()
    keep_time_by(clock)
    yield clock
    keep_time_by(run_clock.WALL)


def test_a_run_keeps_the_walls_time_while_other_work_leaves_it_a_processor():
    machine = _Machine()
    clock = machine.clock()

    machine.spend(10.0, others_hold=15.0)

    assert clock.seconds() == pytest.approx(10.0)


def test_a_run_has_none_of_the_time_other_work_holds_every_processor_for():
    machine = _Machine()
    clock = machine.clock()

    machine.spend(300.0, others_hold=16.0)

    assert clock.seconds() == pytest.approx(0.0)


def test_a_run_left_part_of_a_processor_runs_at_that_part_of_its_speed():
    machine = _Machine()
    clock = machine.clock()

    machine.spend(100.0, others_hold=15.75)

    assert clock.seconds() == pytest.approx(25.0)


def test_the_runs_own_work_never_slows_its_clock():
    machine = _Machine()
    clock = machine.clock()

    machine.spend(10.0, others_hold=0.0, run_holds=16.0)

    assert clock.seconds() == pytest.approx(10.0)


def test_each_stretch_is_counted_at_its_own_speed():
    machine = _Machine()
    clock = machine.clock()

    machine.spend(60.0, others_hold=16.0)
    clock.seconds()
    machine.spend(60.0, others_hold=0.0)

    assert clock.seconds() == pytest.approx(60.0)


_CREATE_SUSPENDED = 0x00000004


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 accounting")
def test_the_machines_busy_time_and_a_jobs_are_read_from_windows():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
    job = a_job_whose_processes_end_with_it()
    machine_before = machine_busy_seconds()
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time\nt = time.process_time()\n"
         "while time.process_time() - t < 0.3: pass"],
        creationflags=_CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW)
    try:
        assert kernel32.AssignProcessToJobObject(job, int(worker._handle))
        ntdll.NtResumeProcess(int(worker._handle))
        worker.wait(timeout=120)

        assert busy_seconds_of_job(job) >= 0.25
        assert machine_busy_seconds() - machine_before >= 0.25
    finally:
        worker.kill()
        kernel32.CloseHandle(job)


def test_a_budget_ends_when_the_run_has_had_its_seconds(frozen):
    budget = Budget(120.0)

    frozen.at = 119.9
    assert not budget.expired()
    frozen.at = 120.0
    assert budget.expired()


def test_a_budget_says_what_is_left_of_it(frozen):
    budget = Budget(120.0)
    frozen.at = 100.0

    assert budget.left() == pytest.approx(20.0)


def test_a_run_that_was_never_given_a_clock_counts_on_the_wall():
    assert run_clock.now() == pytest.approx(run_clock.WALL.seconds(), abs=1.0)


def test_a_test_times_out_once_the_run_has_had_its_seconds(frozen):
    timed_out = threading.Event()
    cancel = watch_for_a_timeout(240.0, timed_out.set, every_s=0.01)
    try:
        assert not timed_out.wait(0.2)
        frozen.at = 240.0
        assert timed_out.wait(5.0)
    finally:
        cancel()


def test_a_cancelled_timeout_never_fires(frozen):
    timed_out = threading.Event()

    watch_for_a_timeout(240.0, timed_out.set, every_s=0.01)()
    frozen.at = 240.0

    assert not timed_out.wait(0.2)
