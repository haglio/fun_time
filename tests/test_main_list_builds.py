from __future__ import annotations

import threading
from dataclasses import replace

from app_support.threading_utils import wait_until

from fun_time.main_list_builds import BuildsOffTheLoop, MainListBuild

SETTLE_S = 5.0


def _main_list_build(tmp_path, **settings) -> MainListBuild:
    return MainListBuild(scripted_filter=False, main_sources=str(tmp_path / "library"),
                         state_dir=tmp_path, main_player_cmd_file=tmp_path / "cmd.txt",
                         **settings)


def test_a_build_replacing_one_that_owed_a_jump_to_the_top_still_owes_it(tmp_path):
    owed = _main_list_build(tmp_path, start_at_top=True)
    newer = _main_list_build(tmp_path, recent=True)

    assert newer.replacing(owed) == replace(newer, start_at_top=True)


class _Build:
    def __init__(self, name: str, ran: list[str], *, gate: threading.Event | None = None) -> None:
        self.name = name
        self._ran = ran
        self._gate = gate
        self.started = threading.Event()

    def run(self) -> None:
        self.started.set()
        if self._gate is not None:
            self._gate.wait(SETTLE_S)
        self._ran.append(self.name)

    def replacing(self, waiting: _Build) -> _Build:
        return _Build(f"{self.name} after {waiting.name}", self._ran)


def test_a_build_is_handed_off_and_never_waited_for():
    ran: list[str] = []
    gate = threading.Event()
    slow = _Build("slow", ran, gate=gate)

    BuildsOffTheLoop().build(slow)

    assert slow.started.wait(SETTLE_S)
    assert ran == []
    gate.set()
    wait_until(lambda: ran == ["slow"], timeout=SETTLE_S)


def test_builds_asked_for_while_one_runs_come_down_to_the_newest():
    ran: list[str] = []
    gate = threading.Event()
    slow = _Build("slow", ran, gate=gate)
    builds = BuildsOffTheLoop()
    builds.build(slow)
    assert slow.started.wait(SETTLE_S)

    builds.build(_Build("second", ran))
    builds.build(_Build("third", ran))
    gate.set()

    wait_until(lambda: len(ran) == 2, timeout=SETTLE_S)
    assert ran == ["slow", "third after second"]


class _BrokenBuild(_Build):
    def run(self) -> None:
        self.started.set()
        raise OSError("the drive went away")


def test_a_build_that_fails_is_logged_and_the_next_still_runs(caplog):
    ran: list[str] = []
    broken = _BrokenBuild("broken", ran)
    builds = BuildsOffTheLoop()
    builds.build(broken)
    assert broken.started.wait(SETTLE_S)

    builds.build(_Build("next", ran))

    wait_until(lambda: ran == ["next"], timeout=SETTLE_S)
    assert "the drive went away" in caplog.text


def test_settling_with_nothing_ever_asked_for_returns_at_once():
    BuildsOffTheLoop().settle()


def test_no_thread_is_left_running_once_every_build_has_run():
    before = set(threading.enumerate())
    builds = BuildsOffTheLoop()
    builds.build(_Build("only", []))

    builds.settle()

    wait_until(lambda: set(threading.enumerate()) <= before, timeout=SETTLE_S)


def test_settle_waits_for_a_build_already_running():
    ran: list[str] = []
    gate = threading.Event()
    slow = _Build("slow", ran, gate=gate)
    builds = BuildsOffTheLoop()
    builds.build(slow)
    assert slow.started.wait(SETTLE_S)
    settled = threading.Event()

    waiter = threading.Thread(target=lambda: (builds.settle(), settled.set()))
    waiter.start()
    try:
        assert not settled.wait(0.05)
        gate.set()
        assert settled.wait(SETTLE_S)
    finally:
        waiter.join(SETTLE_S)
    assert ran == ["slow"]


def test_settle_waits_through_every_build_coalesced_in_while_it_waited():
    ran: list[str] = []
    gate = threading.Event()
    slow = _Build("slow", ran, gate=gate)
    builds = BuildsOffTheLoop()
    builds.build(slow)
    assert slow.started.wait(SETTLE_S)
    builds.build(_Build("second", ran))
    gate.set()

    builds.settle()

    assert ran == ["slow", "second"]
