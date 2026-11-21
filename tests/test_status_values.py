import os

import pytest
from app_support.file_channel import publish_whole, read_key_values

from fun_time.funestra_status import read_main_funestra_status
from fun_time.status_values import StatusValues

LONG_AGO = 1_000_000.0


def _published_long_ago(path, text: str) -> None:
    publish_whole(path, text)
    os.utime(path, (LONG_AGO, LONG_AGO))


class CountsReads:
    def __init__(self) -> None:
        self.reads = 0

    def __call__(self, path):
        self.reads += 1
        return read_key_values(path)


def test_an_unchanged_status_file_is_read_once_however_many_followers_ask(tmp_path):
    status = tmp_path / "main_funestra_status.txt"
    _published_long_ago(status, "speed=1.0\nlocked=1\n")
    counted = CountsReads()
    values = StatusValues(read=counted)

    for _ in range(12):
        assert values.read(status) == {"speed": "1.0", "locked": "1"}

    assert counted.reads == 1


def test_a_replaced_status_file_is_read_again(tmp_path):
    status = tmp_path / "main_funestra_status.txt"
    _published_long_ago(status, "speed=1.0\n")
    counted = CountsReads()
    values = StatusValues(read=counted)
    values.read(status)

    _published_long_ago(status, "speed=2.0\n")

    assert values.read(status) == {"speed": "2.0"}
    assert counted.reads == 2


def test_a_file_read_in_the_tick_it_was_written_is_read_again_until_that_tick_is_past(tmp_path):
    status = tmp_path / "main_funestra_status.txt"
    _published_long_ago(status, "locked=0\n")
    now = [LONG_AGO + 0.001]
    counted = CountsReads()
    values = StatusValues(read=counted, clock=lambda: now[0])

    values.read(status)
    values.read(status)
    now[0] = LONG_AGO + 1.0
    for _ in range(3):
        values.read(status)

    assert counted.reads == 3


def test_a_file_published_a_tenth_of_a_second_ago_is_read_once_however_often_asked(tmp_path):
    status = tmp_path / "main_funestra_status.txt"
    _published_long_ago(status, "speed=1.0\n")
    counted = CountsReads()
    values = StatusValues(read=counted, clock=lambda: LONG_AGO + 0.1)

    for _ in range(5):
        values.read(status)

    assert counted.reads == 1


def test_a_read_that_failed_is_tried_again_on_the_next_ask(tmp_path):
    status = tmp_path / "main_funestra_status.txt"
    values = StatusValues()

    with pytest.raises(FileNotFoundError):
        values.read(status)
    publish_whole(status, "speed=1.5\n")

    assert values.read(status) == {"speed": "1.5"}


def test_what_one_follower_changes_in_its_values_no_other_sees(tmp_path):
    status = tmp_path / "main_funestra_status.txt"
    _published_long_ago(status, "speed=1.0\n")
    values = StatusValues()

    values.read(status)["speed"] = "9.9"

    assert values.read(status) == {"speed": "1.0"}


def test_a_main_funestra_that_has_not_written_yet_reads_as_its_fallback(tmp_path):
    assert read_main_funestra_status(tmp_path / "not_yet.txt").speed == 1.0
