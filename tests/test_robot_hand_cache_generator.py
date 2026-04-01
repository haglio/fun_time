from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from fun_time.robot_hand.cache_generator import ensure_clip_cached
from fun_time.robot_hand.frame_cache import read_rhcache_meta


def _make_frames(count: int, width: int = 8, height: int = 6) -> list[np.ndarray]:
    return [np.random.randint(0, 256, (height, width, 3), dtype=np.uint8) for _ in range(count)]


def test_ensure_clip_cached_generates_when_missing(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.touch()
    cache_dir = tmp_path / ".rhcache"

    fake_frames = _make_frames(3, width=16, height=12)

    with patch(
        "fun_time.robot_hand.cache_generator.decode_video_to_numpy_frames",
        return_value=fake_frames,
    ):
        result = ensure_clip_cached(video_path, cache_dir)

    assert result.exists()
    assert result.name == "clip.rhcache"
    meta = read_rhcache_meta(result)
    assert meta["frame_count"] == 3
    assert meta["source"] == "clip.mp4"


def test_ensure_clip_cached_reuses_existing(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.touch()
    cache_dir = tmp_path / ".rhcache"

    fake_frames = _make_frames(3)
    with patch(
        "fun_time.robot_hand.cache_generator.decode_video_to_numpy_frames",
        return_value=fake_frames,
    ) as mock_decode:
        ensure_clip_cached(video_path, cache_dir)
        assert mock_decode.call_count == 1

        ensure_clip_cached(video_path, cache_dir)
        assert mock_decode.call_count == 1
