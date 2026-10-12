from __future__ import annotations

import threading
from dataclasses import replace

from app_support.threading_utils import wait_until
from funestra.modes import LengthMode

from fun_time.main_list import MainList, MainListSettings
from fun_time.main_list_builds import BuildsOffTheLoop, MainListBuild

SETTLE_S = 5.0


def _main_list_build(tmp_path, settings: MainListSettings, **build) -> MainListBuild:
    return MainListBuild(main_list=MainList(sources=str(tmp_path / "library"), state_dir=tmp_path),
                         settings=settings, main_funestra_cmd_file=tmp_path / "cmd.txt", **build)


def test_a_rebuild_writes_the_list_from_the_video_on_screen(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    videos = [library / f"scene {number}.mp4" for number in range(5)]
    for video in videos:
        video.write_text("x", encoding="utf-8")
    build = _main_list_build(tmp_path, MainListSettings(shuffle_seed=7), start_from=str(videos[3]))

    build.run()

    assert build.main_list.file.read_text(encoding="utf-8").splitlines()[0] == str(videos[3])


def test_a_rebuild_tells_the_main_funestra_what_its_buttons_built_it_under(tmp_path):
    """Kino cannot read F-mode or the length off the list it is handed, so its
    HUD only knows them because it is told, on the write the reload rides."""
    build = _main_list_build(tmp_path, MainListSettings(scripted_filter=True,
                                                        length=LengthMode.CLIPS))

    build.run()

    assert build.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines() == [
        "RELOAD_PLAYLIST", "SET_F_MODE 1", "SET_LENGTH_MODE clips"]


def test_a_build_replacing_one_that_owed_a_jump_to_the_top_still_owes_it(tmp_path):
    owed = _main_list_build(tmp_path, MainListSettings(), start_at_top=True)
    newer = _main_list_build(tmp_path, MainListSettings(latest=True))

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
