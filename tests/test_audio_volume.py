from __future__ import annotations

from pathlib import Path

from fun_time.audio_volume import MAX_VOLUME, read_volume, write_volume


def test_write_then_read_roundtrips_the_level(tmp_path: Path):
    path = tmp_path / "nested" / "audio_volume.txt"

    write_volume(path, 30)

    assert read_volume(path) == 30


def test_an_unpublished_level_reads_as_full_volume(tmp_path: Path):
    """The companion may start before the bridge has ever published a level."""
    assert read_volume(tmp_path / "missing.txt") == MAX_VOLUME


def test_a_corrupt_level_reads_as_full_volume(tmp_path: Path):
    """A half-written file must not silence the session."""
    path = tmp_path / "audio_volume.txt"
    path.write_text("", encoding="utf-8")

    assert read_volume(path) == MAX_VOLUME


def test_a_byte_order_mark_does_not_corrupt_the_level(tmp_path: Path):
    path = tmp_path / "audio_volume.txt"
    path.write_text("﻿40\n", encoding="utf-8")

    assert read_volume(path) == 40
