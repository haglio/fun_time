from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from fun_time.robot_hand.video import load_clip_frames


def _make_frames(count: int, width: int = 8, height: int = 6) -> list[np.ndarray]:
    return [np.random.randint(0, 256, (height, width, 3), dtype=np.uint8) for _ in range(count)]


def test_load_clip_frames_from_cache(tmp_path):
    from fun_time.robot_hand.frame_cache import write_rhcache

    video_path = tmp_path / "clip.mp4"
    video_path.touch()
    cache_dir = tmp_path / ".rhcache"
    cache_dir.mkdir()

    frames_np = _make_frames(4, width=10, height=8)
    write_rhcache(frames_np, cache_dir / "clip.rhcache", source_name="clip.mp4", lossless=True)

    result = load_clip_frames(video_path, cache_dir)
    assert len(result) == 4
    for frame in result:
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (8, 10, 3)


def test_load_clip_frames_falls_back_to_ffmpeg(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.touch()
    cache_dir = tmp_path / ".rhcache"

    fake_frames = _make_frames(3)

    with patch(
        "fun_time.robot_hand.video.decode_video_to_numpy_frames",
        return_value=fake_frames,
    ):
        result = load_clip_frames(video_path, cache_dir)

    assert len(result) == 3
    for frame in result:
        assert isinstance(frame, np.ndarray)
