"""Unit tests: an integration run's waits are counted in the seconds it could have run."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time

import pytest

from tests.integration import run_clock
from tests.integration.run_clock import (
    Budget,
    OnTimeClock,
    RunClock,
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


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 accounting")
def test_the_machines_busy_time_is_read_from_windows():
    machine_before = machine_busy_seconds()

    subprocess.run([sys._base_executable, "-c", "import time\nt = time.process_time()\n"
                    "while time.process_time() - t < 0.3: pass"],
                   creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=120)

    assert machine_busy_seconds() - machine_before >= 0.25


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


class _Samples:
    def __init__(self, *samples: tuple[float, set[int]]) -> None:
        self.at = 0.0
        self._samples = iter(samples)
        self._waiting: set[int] = set()
        self.done = threading.Event()

    def wall(self) -> float:
        return self.at

    def waiting(self) -> set[int]:
        return self._waiting

    def nap(self, _seconds: float) -> None:
        sample = next(self._samples, None)
        if sample is None:
            self.done.set()
            time.sleep(0.001)
            return
        wait, self._waiting = sample
        self.at += wait


def _after(*samples: tuple[float, set[int]]) -> OnTimeClock:
    script = _Samples(*samples)
    with OnTimeClock(slack_s=1 / 60, nap_s=0.001, nap=script.nap, wall=script.wall,
                     waiting=script.waiting) as clock:
        assert script.done.wait(5.0)
        return clock


def test_a_run_whose_thread_gets_the_processor_within_the_slack_has_every_second_on_time():
    assert _after((0.001, set()), (0.002, set()), (0.015, set())).seconds() == pytest.approx(0.018)


def test_a_stretch_the_runs_thread_waited_longer_than_the_slack_for_is_not_on_time():
    assert _after((0.001, set()), (0.4, set()), (0.001, set())).seconds() == pytest.approx(0.002)


def test_a_stretch_in_which_another_thread_of_the_run_waited_longer_than_the_slack_is_not_on_time():
    clock = _after((0.002, set()), (0.002, {7}), (0.010, {7}), (0.010, {7}), (0.002, set()))

    assert clock.seconds() == pytest.approx(0.016)


def test_an_on_time_clock_counts_the_real_seconds_its_thread_is_given_until_it_is_closed():
    with OnTimeClock(slack_s=1 / 60) as clock:
        given_up_at = time.monotonic() + 30.0
        while clock.seconds() < 0.05 and time.monotonic() < given_up_at:
            time.sleep(0.01)
        assert clock.seconds() >= 0.05

    assert not [thread for thread in threading.enumerate() if thread.name == "on-time clock"]


def test_the_run_is_held_while_a_thread_of_it_has_waited_longer_than_the_slack():
    assert _after((0.002, {7}), (0.020, {7})).held()
    assert not _after((0.002, {7}), (0.010, {7})).held()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 scheduler")
def test_the_on_time_clock_reads_this_processes_threads_and_their_states_from_windows():
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentThread.restype = wt.HANDLE

    assert threading.get_native_id() in run_clock._thread_ids_of(os.getpid())
    assert run_clock._scheduler_state(kernel32.GetCurrentThread()) == 2  # Running
