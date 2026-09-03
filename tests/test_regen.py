from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

from fun_time.media_metadata import metadata_path_for
from fun_time.regen import (
    build_payload,
    build_regen_url,
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
    meta_file = metadata_path_for(video, metadata_root)
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")
    return media_root, metadata_root, video


def test_regen_url_for_video_returns_image_url_for_source_image(tmp_path: Path):
    _, metadata_root, video = _setup(tmp_path, "portrait", "abc_topaz.mp4", IMAGE_META)

    url = regen_url_for_video(
        video, metadata_root=metadata_root, video_url=VIDEO_URL, image_url=IMAGE_URL
    )

    assert url.startswith(IMAGE_URL + "#ft=")


def test_regen_url_for_video_returns_empty_for_non_provider(tmp_path: Path):
    media_root, metadata_root, _ = _setup(tmp_path, "portrait", "abc_topaz.mp4", IMAGE_META)
    provider2 = media_root / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider2" / "x.mp4"

    url = regen_url_for_video(
        provider2, metadata_root=metadata_root, video_url=VIDEO_URL, image_url=IMAGE_URL
    )

    assert url == ""


def test_regen_url_for_video_returns_empty_for_a_sidecar_holding_only_a_kind(tmp_path: Path):
    """Evolver records what kind every video is, so a clip whose generation was
    never scraped now HAS a sidecar. There is nothing to regenerate from in it,
    and a URL built off it would carry no prompt at all."""
    _media_root, metadata_root, video = _setup(
        tmp_path, "portrait", "abc_topaz.mp4", {"video": {"type": "short"}},
    )

    url = regen_url_for_video(
        video, metadata_root=metadata_root, video_url=VIDEO_URL, image_url=IMAGE_URL
    )

    assert url == ""


def test_regen_url_for_video_returns_empty_when_metadata_absent(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider" / "missing_topaz.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")

    url = regen_url_for_video(
        video, metadata_root=metadata_root, video_url=VIDEO_URL, image_url=IMAGE_URL
    )

    assert url == ""
