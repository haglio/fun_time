"""The pixels one process draws and another shows: the headset's library browser."""
from __future__ import annotations

import pytest

from fun_time_vr.frame_channel import FrameReader, FrameWriter


def _pixels(width: int, height: int, value: int) -> bytes:
    return bytes([value]) * (width * height * 4)


@pytest.fixture
def channel(tmp_path):
    writer = FrameWriter(tmp_path / "frame.bin", max_pixels=64)
    reader = FrameReader(tmp_path / "frame.bin")
    yield writer, reader
    reader.close()
    writer.close()


def test_a_frame_written_is_the_frame_read(channel):
    writer, reader = channel

    writer.write(3, 4, 2, _pixels(4, 2, 7))

    assert reader.latest(3) == (4, 2, _pixels(4, 2, 7))


def test_a_frame_drawn_for_an_earlier_opening_is_not_shown_for_this_one(channel):
    writer, reader = channel

    writer.write(2, 4, 2, _pixels(4, 2, 7))

    assert reader.latest(3) is None


def test_a_frame_already_read_is_not_read_again(channel):
    writer, reader = channel
    writer.write(3, 4, 2, _pixels(4, 2, 7))
    reader.latest(3)

    assert reader.latest(3) is None

    writer.write(3, 4, 2, _pixels(4, 2, 9))
    assert reader.latest(3) == (4, 2, _pixels(4, 2, 9))


def test_a_frame_still_being_written_is_left_for_the_next_look(channel):
    writer, reader = channel
    writer.write(3, 4, 2, _pixels(4, 2, 7))

    with writer.writing():
        assert reader.latest(3) is None


def test_a_reader_that_looks_before_anything_draws_sees_nothing_until_it_does(tmp_path):
    reader = FrameReader(tmp_path / "frame.bin")
    try:
        assert reader.latest(1) is None

        writer = FrameWriter(tmp_path / "frame.bin", max_pixels=64)
        try:
            assert reader.latest(1) is None
            writer.write(1, 2, 2, _pixels(2, 2, 5))
            assert reader.latest(1) == (2, 2, _pixels(2, 2, 5))
        finally:
            writer.close()
    finally:
        reader.close()


def test_a_frame_bigger_than_the_channel_is_refused(channel):
    writer, _reader = channel

    with pytest.raises(ValueError):
        writer.write(1, 10, 10, _pixels(10, 10, 1))


def test_a_new_writer_starts_the_channel_empty(tmp_path):
    first = FrameWriter(tmp_path / "frame.bin", max_pixels=64)
    first.write(1, 2, 2, _pixels(2, 2, 5))
    first.close()

    second = FrameWriter(tmp_path / "frame.bin", max_pixels=64)
    reader = FrameReader(tmp_path / "frame.bin")
    try:
        assert reader.latest(1) is None
    finally:
        reader.close()
        second.close()
