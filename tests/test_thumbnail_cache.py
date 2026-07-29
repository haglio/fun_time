from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from fun_time.thumbnail_cache import (
    cached_thumbnail,
    prewarm_thumbnails,
    thumbnail_for,
    thumbnail_path,
)


def _make_video(path: Path, *, width: int = 64, height: int = 48, frames: int = 10) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (width, height))
    assert writer.isOpened(), "could not open a VideoWriter for the test clip"
    for i in range(frames):
        writer.write(np.full((height, width, 3), (i * 25) % 256, dtype=np.uint8))
    writer.release()


def test_thumbnail_path_is_stable_and_scoped_to_cache_dir(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    cache = tmp_path / "cache"

    first = thumbnail_path(video, cache)
    second = thumbnail_path(video, cache)

    assert first == second
    assert first.parent == cache
    assert first.suffix == ".jpg"


def test_thumbnail_path_changes_when_the_video_is_modified(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    cache = tmp_path / "cache"
    before = thumbnail_path(video, cache)

    stat = video.stat()
    os.utime(video, (stat.st_atime, stat.st_mtime + 60))

    assert thumbnail_path(video, cache) != before


def test_thumbnail_for_extracts_a_frame_scaled_within_bounds(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    _make_video(video, width=64, height=48)
    cache = tmp_path / "cache"

    thumb = thumbnail_for(video, cache, max_size=32)

    assert thumb is not None and thumb.is_file()
    with Image.open(thumb) as img:
        assert max(img.size) <= 32
        assert img.size[0] / img.size[1] == pytest.approx(64 / 48, abs=0.2)


def test_thumbnail_for_reuses_the_cached_file(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    _make_video(video)
    cache = tmp_path / "cache"

    first = thumbnail_for(video, cache, max_size=32)
    first.write_bytes(b"SENTINEL")  # mark it so regeneration would be visible

    second = thumbnail_for(video, cache, max_size=32)

    assert second == first
    assert second.read_bytes() == b"SENTINEL"  # served from cache, not rebuilt


def test_thumbnail_for_returns_none_for_an_unreadable_video(tmp_path: Path):
    bad = tmp_path / "garbage.mp4"
    bad.write_bytes(b"not a real video")

    assert thumbnail_for(bad, tmp_path / "cache") is None


def test_thumbnail_for_returns_none_for_a_missing_video(tmp_path: Path):
    assert thumbnail_for(tmp_path / "nope.mp4", tmp_path / "cache") is None


def test_cached_thumbnail_returns_the_file_only_when_it_exists(tmp_path: Path):
    """The HUD paints with this, so it must never extract — just report the cached
    file if the prewarm has already made it, else None."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    cache = tmp_path / "cache"

    assert cached_thumbnail(video, cache) is None  # nothing extracted yet

    dest = thumbnail_path(video, cache)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"JPEG")  # as if the prewarm produced it

    assert cached_thumbnail(video, cache) == dest


def test_prewarming_covers_every_path_it_is_handed(tmp_path: Path):
    """Stills are extracted up front so a first use never blocks on a frame grab
    — the source of the multi-second map lag.  Each caller decides *which*
    videos: a satellite warms every clip in its library, the main library browser
    warms one per handle, off the cheapest rendition of it."""
    cache_dir = tmp_path / "thumbs"
    warmed: list[tuple[str, object]] = []

    prewarm_thumbnails(
        [r"C:\videos\a.mp4", r"C:\videos\b.mp4", r"C:\videos\c.mp4"], cache_dir,
        thumbnailer=lambda path, cache: warmed.append((path, cache)), sleep_fn=lambda _s: None,
    )

    assert [Path(p).name for p, _cache in warmed] == ["a.mp4", "b.mp4", "c.mp4"]
    assert all(cache == cache_dir for _p, cache in warmed)
