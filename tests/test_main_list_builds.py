from __future__ import annotations

import logging
import os
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


def _full_videos_for_want_of_a_record(tmp_path, *names: str) -> list:
    library = tmp_path / "library"
    library.mkdir()
    videos = [library / name for name in names]
    for video in videos:
        video.write_text("x", encoding="utf-8")
    return videos


def test_a_rebuild_writes_the_list_from_the_video_on_screen(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    videos = [library / f"scene {number}.mp4" for number in range(5)]
    for video in videos:
        video.write_text("x", encoding="utf-8")
    build = _main_list_build(tmp_path, MainListSettings(shuffle_seed=7), on_screen=str(videos[3]))

    build.run()

    assert build.main_list.file.read_text(encoding="utf-8").splitlines()[0] == str(videos[3])


def test_a_latest_list_started_at_the_top_opens_on_the_newest_arrival(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    on_screen, arrival = library / "video one.mp4", library / "video two.mp4"
    for arrived, video in enumerate((on_screen, arrival)):
        video.write_text("x", encoding="utf-8")
        os.utime(video, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
    build = _main_list_build(tmp_path, MainListSettings(latest=True), on_screen=str(on_screen),
                             start_at_top=True)

    build.run()

    assert build.main_list.file.read_text(encoding="utf-8").splitlines()[0] == str(arrival)
    assert str(arrival) in build.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines()[-1]


def test_a_rebuild_tells_the_main_funestra_its_f_mode_on_the_write_the_reload_rides(tmp_path):
    build = _main_list_build(tmp_path, MainListSettings(scripted_filter=True,
                                                        length=LengthMode.CLIPS))

    build.run()

    assert build.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines()[:2] == [
        "RELOAD_PLAYLIST", "SET_F_MODE 1"]


def test_a_press_that_leaves_no_video_holds_the_one_on_screen_locked_and_says_so(tmp_path, caplog):
    on_screen, _other = _full_videos_for_want_of_a_record(
        tmp_path, "scene one.mp4", "scene two.mp4")
    build = _main_list_build(tmp_path, MainListSettings(length=LengthMode.CLIPS),
                             on_screen=str(on_screen))

    with caplog.at_level(logging.WARNING):
        build.run()

    assert build.main_list.file.read_text(encoding="utf-8").splitlines() == [str(on_screen)]
    assert build.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines()[-1] == "LOCK_ON"
    assert [record.getMessage() for record in caplog.records
            if record.levelno == logging.WARNING] == ["Including no other videos"]


def test_a_press_that_leaves_videos_says_what_it_asked_for(tmp_path, caplog):
    library = tmp_path / "library"
    library.mkdir()
    (library / "scene one.mp4").write_text("x", encoding="utf-8")
    build = _main_list_build(tmp_path, MainListSettings(), says="Clips + Full")

    with caplog.at_level(logging.INFO):
        build.run()

    assert [record.getMessage() for record in caplog.records
            if record.levelno >= logging.INFO] == ["Clips + Full"]


def test_a_press_that_leaves_no_video_says_only_that(tmp_path, caplog):
    _full_videos_for_want_of_a_record(tmp_path, "scene one.mp4")
    build = _main_list_build(tmp_path, MainListSettings(length=LengthMode.CLIPS),
                             says="Clips only")

    with caplog.at_level(logging.INFO):
        build.run()

    assert [record.getMessage() for record in caplog.records
            if record.levelno >= logging.INFO] == ["Including no other videos"]


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


def test_a_build_held_up_by_a_slow_drive_is_never_waited_for():
    ran: list[str] = []
    gate = threading.Event()
    on_a_slow_drive = _Build("slow", ran, gate=gate)

    BuildsOffTheLoop().build(on_a_slow_drive)

    assert on_a_slow_drive.started.wait(SETTLE_S)
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
