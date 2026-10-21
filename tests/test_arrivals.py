from __future__ import annotations

import os
import threading
from pathlib import Path

from fun_time.arrivals import Arrivals, keep_looking
from fun_time.modes import collect_video_files
from tests.drive_fakes import files_that_never_answer


def _video(folder: Path, name: str) -> str:
    path = folder / name
    path.write_bytes(b"\x00")
    return str(path)


def _watching(folder: Path) -> Arrivals:
    _video(folder, "scene one.mp4")
    arrivals = Arrivals(lambda: collect_video_files(str(folder)))
    arrivals.look()
    return arrivals


class TestWhatArrives:
    def test_what_is_there_when_it_first_looks_is_the_library_not_an_arrival(self, tmp_path):
        arrivals = _watching(tmp_path)

        arrivals.look()

        assert arrivals.waiting() == []

    def test_one_that_comes_in_between_looks_waits(self, tmp_path):
        arrivals = _watching(tmp_path)

        newcomer = _video(tmp_path, "scene two.mp4")
        arrivals.look()

        assert arrivals.waiting() == [newcomer]

    def test_several_in_one_look_wait_oldest_first(self, tmp_path):
        arrivals = _watching(tmp_path)

        newer, older = _video(tmp_path, "alpha.mp4"), _video(tmp_path, "beta.mp4")
        os.utime(newer, (2_000_000_000, 2_000_000_000))
        os.utime(older, (1_000_000_000, 1_000_000_000))
        arrivals.look()

        assert arrivals.waiting() == [older, newer]

    def test_ones_from_later_looks_wait_behind_the_earlier_ones(self, tmp_path):
        arrivals = _watching(tmp_path)
        first = _video(tmp_path, "beta.mp4")
        os.utime(first, (2_000_000_000, 2_000_000_000))
        arrivals.look()

        second = _video(tmp_path, "alpha.mp4")
        os.utime(second, (1_000_000_000, 1_000_000_000))
        arrivals.look()

        assert arrivals.waiting() == [first, second]

    def test_one_that_leaves_the_library_before_it_is_taken_stops_waiting(self, tmp_path):
        arrivals = _watching(tmp_path)
        newcomer = _video(tmp_path, "scene two.mp4")
        arrivals.look()

        Path(newcomer).unlink()
        arrivals.look()

        assert arrivals.waiting() == []

    def test_those_taken_stop_waiting_and_one_come_since_goes_on_waiting(self, tmp_path):
        arrivals = _watching(tmp_path)
        taken = _video(tmp_path, "scene two.mp4")
        arrivals.look()
        judged = arrivals.waiting()
        since = _video(tmp_path, "scene three.mp4")
        arrivals.look()

        arrivals.take(judged)

        assert judged == [taken]
        assert arrivals.waiting() == [since]

    def test_one_taken_while_the_library_is_being_listed_does_not_come_back(self, tmp_path):
        listings: list = []

        def list_the_library() -> list[str]:
            listed = collect_video_files(str(tmp_path))
            for during in listings:
                during()
            return listed

        _video(tmp_path, "scene one.mp4")
        arrivals = Arrivals(list_the_library)
        arrivals.look()
        _video(tmp_path, "scene two.mp4")
        arrivals.look()
        listings.append(lambda: arrivals.take(arrivals.waiting()))

        arrivals.look()

        assert arrivals.waiting() == []

    def test_a_look_reads_the_folder_listings_and_never_asks_about_a_file(self, tmp_path):
        arrivals = _watching(tmp_path)
        older, newer = _video(tmp_path, "beta.mp4"), _video(tmp_path, "alpha.mp4")
        os.utime(older, (1_000_000_000, 1_000_000_000))

        with files_that_never_answer(tmp_path):
            arrivals.look()

        assert arrivals.waiting() == [older, newer]


class TestKeepingLooking:
    def test_looks_at_once_and_then_every_so_often_until_it_is_stopped(self):
        stop, looks = threading.Event(), []

        def look() -> None:
            looks.append(1)
            if len(looks) == 3:
                stop.set()

        keep_looking(look, stop, every_s=0)

        assert len(looks) == 3

    def test_a_look_that_fails_is_logged_and_the_next_one_still_comes(self, caplog):
        stop, looks = threading.Event(), []

        def look() -> None:
            looks.append(1)
            if len(looks) == 1:
                raise RuntimeError("a listing that broke")
            stop.set()

        keep_looking(look, stop, every_s=0)

        assert len(looks) == 2
        assert "a listing that broke" in caplog.text


class TestADriveThatListsTooLittle:
    def test_a_look_that_lists_nothing_changes_nothing(self):
        listings = iter([["C:/library/alpha.mp4"], [], ["C:/library/alpha.mp4",
                                                        "C:/library/beta.mp4"]])
        arrivals = Arrivals(lambda: next(listings))

        for _ in range(3):
            arrivals.look()

        assert arrivals.waiting() == ["C:/library/beta.mp4"]

    def test_one_missing_from_a_look_is_no_arrival_when_it_is_listed_again(self):
        listings = iter([["C:/library/alpha.mp4", "C:/library/beta.mp4"],
                         ["C:/library/beta.mp4"],
                         ["C:/library/alpha.mp4", "C:/library/beta.mp4"]])
        arrivals = Arrivals(lambda: next(listings))

        for _ in range(3):
            arrivals.look()

        assert arrivals.waiting() == []
