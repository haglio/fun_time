from __future__ import annotations

import json
from pathlib import Path

from fun_time.media_metadata import load_metadata, metadata_path_for

VIDEO_ONLY_META = {
    "video": {
        "prompt": "JUST A VIDEO",
        "model": "Video v3",
        "action": "Pov Epsilon",
        "resolution": "1280x720",
        "aspect_ratio": "16:9",
        "quality": "720p",
        "seed": "4029423637",
        "created": "2026-03-13",
    }
}


# --- metadata_path_for ---


def test_metadata_path_mirrors_media_tree_under_metadata_root(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider" / "abc_topaz.mp4"

    result = metadata_path_for(video, media_root, metadata_root)

    assert result == metadata_root / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider" / "abc_topaz.json"


def test_metadata_path_returns_none_when_outside_media_root(tmp_path: Path):
    media_root = tmp_path / "media"
    metadata_root = tmp_path / "meta"
    outside = tmp_path / "elsewhere" / "clip.mp4"

    assert metadata_path_for(outside, media_root, metadata_root) is None


def test_metadata_path_returns_none_when_roots_missing(tmp_path: Path):
    assert metadata_path_for(tmp_path / "x.mp4", None, None) is None


# --- load_metadata ---


def test_load_metadata_reads_dict(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(VIDEO_ONLY_META), encoding="utf-8")

    assert load_metadata(p) == VIDEO_ONLY_META


def test_load_metadata_returns_empty_on_missing_or_invalid(tmp_path: Path):
    assert load_metadata(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_metadata(bad) == {}
