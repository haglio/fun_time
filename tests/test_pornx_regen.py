from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

from fun_time.provider_regen import (
    build_payload,
    build_regen_url,
    load_metadata,
    metadata_path_for,
    regen_url_for_video,
)

VIDEO_URL = "https://example.com/video"
IMAGE_URL = "https://example.com/create"

IMAGE_META = {
    "video": {
        "prompt": "VIDEO PROMPT",
        "model": "Realism",
        "action": "Oral Insertion",
        "resolution": "544x816",
        "aspect_ratio": "2:3",
        "quality": "720p",
        "seed": "3443370201",
        "created": "2025-12-28",
    },
    "source_image": {
        "positive_prompt": "POSITIVE",
        "negative_prompt": "NEGATIVE",
        "model": "Realism",
        "resolution": "1280x1920",
        "aspect_ratio": "2:3",
        "quality": "Best",
        "seed": "2559368667",
        "created": "2025-12-28",
        "style": "Default",
        "creativity": "7",
    },
}

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


def _decode(url: str) -> dict:
    return json.loads(unquote(url.split("#ft=", 1)[1]))


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


# --- build_payload ---


def test_payload_for_source_image_targets_image_with_both_prompts():
    payload = build_payload(IMAGE_META)

    assert payload["kind"] == "image"
    assert payload["positive"] == "POSITIVE"
    assert payload["negative"] == "NEGATIVE"
    assert payload["video_prompt"] == "VIDEO PROMPT"
    assert ["Quality", "Best"] in payload["settings"]
    assert ["Style", "Default"] in payload["settings"]
    assert ["Model", "Realism"] in payload["settings"]
    # video_settings carry the video step's params for the post-it
    assert ["Action", "Oral Insertion"] in payload["video_settings"]


def test_payload_for_video_only_targets_video_no_negative():
    payload = build_payload(VIDEO_ONLY_META)

    assert payload["kind"] == "video"
    assert payload["positive"] == "JUST A VIDEO"
    assert payload["negative"] == ""
    assert payload["video_prompt"] == ""
    assert ["Action", "Pov Epsilon"] in payload["settings"]


def test_payload_skips_empty_setting_values():
    meta = {"video": {"prompt": "p", "model": "", "seed": "123"}}
    labels = [pair[0] for pair in build_payload(meta)["settings"]]
    assert "Model" not in labels
    assert "Seed" in labels


# --- build_regen_url ---


def test_regen_url_image_uses_image_base_and_decodable_payload():
    url = build_regen_url(IMAGE_META, video_url=VIDEO_URL, image_url=IMAGE_URL)

    assert url.startswith(IMAGE_URL + "#ft=")
    payload = _decode(url)
    assert payload["kind"] == "image"
    assert payload["negative"] == "NEGATIVE"


def test_regen_url_video_uses_video_base():
    url = build_regen_url(VIDEO_ONLY_META, video_url=VIDEO_URL, image_url=IMAGE_URL)

    assert url.startswith(VIDEO_URL + "#ft=")
    assert _decode(url)["kind"] == "video"


# --- regen_url_for_video (end to end) ---


def _setup(tmp_path: Path, orient: str, name: str, meta: dict) -> tuple[Path, Path, Path]:
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "2_outbox" / "upscaled_by_orientation" / orient / "provider" / name
    meta_file = metadata_path_for(video, media_root, metadata_root)
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")
    return media_root, metadata_root, video


def test_regen_url_for_video_returns_image_url_for_source_image(tmp_path: Path):
    media_root, metadata_root, video = _setup(tmp_path, "portrait", "abc_topaz.mp4", IMAGE_META)

    url = regen_url_for_video(
        video, media_root=media_root, metadata_root=metadata_root, video_url=VIDEO_URL, image_url=IMAGE_URL
    )

    assert url.startswith(IMAGE_URL + "#ft=")


def test_regen_url_for_video_returns_empty_for_non_provider(tmp_path: Path):
    media_root, metadata_root, _ = _setup(tmp_path, "portrait", "abc_topaz.mp4", IMAGE_META)
    provider2 = media_root / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider2" / "x.mp4"

    url = regen_url_for_video(
        provider2, media_root=media_root, metadata_root=metadata_root, video_url=VIDEO_URL, image_url=IMAGE_URL
    )

    assert url == ""


def test_regen_url_for_video_returns_empty_when_metadata_absent(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider" / "missing_topaz.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")

    url = regen_url_for_video(
        video, media_root=media_root, metadata_root=metadata_root, video_url=VIDEO_URL, image_url=IMAGE_URL
    )

    assert url == ""
