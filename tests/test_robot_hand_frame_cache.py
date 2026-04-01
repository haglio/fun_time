from __future__ import annotations

import numpy as np
import pytest

from fun_time.robot_hand.frame_cache import (
    read_rhcache_all_frames,
    read_rhcache_frame,
    read_rhcache_meta,
    write_rhcache,
)


def _make_frames(count: int, width: int = 8, height: int = 6) -> list[np.ndarray]:
    return [np.random.randint(0, 256, (height, width, 3), dtype=np.uint8) for _ in range(count)]


def test_write_and_read_meta(tmp_path):
    frames = _make_frames(5, width=16, height=12)
    cache_path = tmp_path / "clip.rhcache"

    write_rhcache(frames, cache_path, source_name="clip.mp4")

    meta = read_rhcache_meta(cache_path)
    assert meta["width"] == 16
    assert meta["height"] == 12
    assert meta["frame_count"] == 5
    assert meta["source"] == "clip.mp4"


def test_read_single_frame_round_trip_lossless(tmp_path):
    frames = _make_frames(3, width=16, height=12)
    cache_path = tmp_path / "clip.rhcache"
    write_rhcache(frames, cache_path, source_name="clip.mp4", lossless=True)

    for i in range(3):
        recovered = read_rhcache_frame(cache_path, i)
        assert recovered.shape == (12, 16, 3)
        assert recovered.dtype == np.uint8
        np.testing.assert_array_equal(recovered, frames[i])


def test_read_single_frame_lossy_is_close(tmp_path):
    frames = _make_frames(3, width=16, height=12)
    cache_path = tmp_path / "clip.rhcache"
    write_rhcache(frames, cache_path, source_name="clip.mp4", quality=95)

    for i in range(3):
        recovered = read_rhcache_frame(cache_path, i)
        assert recovered.shape == frames[i].shape
        assert recovered.dtype == np.uint8


def test_read_all_frames_lossless(tmp_path):
    frames = _make_frames(4, width=10, height=8)
    cache_path = tmp_path / "clip.rhcache"
    write_rhcache(frames, cache_path, source_name="clip.mp4", lossless=True)

    all_frames = read_rhcache_all_frames(cache_path)
    assert len(all_frames) == 4
    for i, recovered in enumerate(all_frames):
        np.testing.assert_array_equal(recovered, frames[i])


def test_write_empty_frames_raises(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        write_rhcache([], tmp_path / "empty.rhcache")


def test_read_frame_out_of_range(tmp_path):
    frames = _make_frames(2, width=8, height=6)
    cache_path = tmp_path / "clip.rhcache"
    write_rhcache(frames, cache_path, lossless=True)

    with pytest.raises(KeyError):
        read_rhcache_frame(cache_path, 5)
