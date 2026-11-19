from __future__ import annotations

import threading
from pathlib import Path

from fun_time.command_bell import CommandBell, bell_name, ring, wait_out_the_tick
from tests.ahk_script import function_source


class WoundClock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


class RingsThenFallsQuiet:
    def __init__(self, clock: WoundClock, rings: int) -> None:
        self._clock = clock
        self._rings = rings
        self.waits: list[float] = []

    def wait(self, timeout: float) -> bool:
        self.waits.append(round(timeout, 3))
        if self._rings:
            self._rings -= 1
            self._clock.now += 0.01
            return True
        self._clock.now += timeout
        return False


def test_a_command_written_between_ticks_is_answered_at_once_and_the_tick_still_waits_out():
    clock = WoundClock()
    bell = RingsThenFallsQuiet(clock, rings=2)
    answered: list[float] = []

    wait_out_the_tick(bell=bell, stop=threading.Event(), clock=clock,
                      answer=lambda: answered.append(round(clock.now, 3)), tick_s=0.05)

    assert (answered, bell.waits) == ([50.01, 50.02], [0.05, 0.04, 0.03])


def test_a_quiet_tick_answers_nothing_and_waits_once():
    clock = WoundClock()
    bell = RingsThenFallsQuiet(clock, rings=0)
    answered: list[float] = []

    wait_out_the_tick(bell=bell, stop=threading.Event(), clock=clock,
                      answer=lambda: answered.append(clock.now), tick_s=0.05)

    assert (answered, bell.waits) == ([], [0.05])


def test_a_loop_told_to_stop_waits_no_longer():
    clock = WoundClock()
    bell = RingsThenFallsQuiet(clock, rings=5)
    stop = threading.Event()
    stop.set()

    wait_out_the_tick(bell=bell, stop=stop, clock=clock, answer=lambda: None, tick_s=0.05)

    assert bell.waits == []


def test_every_writer_names_the_bell_the_same_way_whatever_its_slashes_and_case():
    assert bell_name(Path("C:/Example/State/dashboard_cmd.txt")) == bell_name(
        "c:\\example\\state\\DASHBOARD_CMD.TXT")
    assert bell_name("C:/Example/State/dashboard_cmd.txt") == (
        "Local\\FunTime_c__example_state_dashboard_cmd_txt")


def test_a_ring_from_another_writer_wakes_the_bell(tmp_path):
    cmd_file = tmp_path / "dashboard_cmd.txt"
    bell = CommandBell(cmd_file)
    try:
        assert bell.wait(0) is False
        assert ring(cmd_file) is True
        assert bell.wait(None) is True
        assert bell.wait(0) is False
    finally:
        bell.close()


def test_a_ring_with_no_session_listening_does_nothing(tmp_path):
    assert ring(tmp_path / "no_session_here.txt") is False


def test_the_hotkey_script_rings_the_bell_by_the_same_name_after_each_command_it_writes():
    queued = function_source("QueueCommand")
    rings = function_source("RingTheCommandBell")

    assert queued.rstrip("}").rstrip().endswith("RingTheCommandBell()")
    assert r'"Local\FunTime_" . RegExReplace(StrLower(DASHBOARD_CMD_FILE), "[^a-z0-9]", "_")' in rings
