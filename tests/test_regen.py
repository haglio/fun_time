from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from fun_time.content import WebProvider
from fun_time.gallery import Gallery
from fun_time.media_metadata import metadata_path_for
from fun_time.regen import (
    build_payload,
    build_regen_url,
    regen_url_for_video,
)
from tests.made_up_gallery import a_row, a_video_from_a_start_image

VIDEO_URL = "https://example.com/video"
IMAGE_URL = "https://example.com/create"
_PROVIDERS = (WebProvider(marker="provider", gallery_url="https://example.com/image/{id}"),)

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
        "action": "Side Epsilon",
        "resolution": "1280x720",
        "aspect_ratio": "16:9",
        "quality": "720p",
        "seed": "4029423637",
        "created": "2026-03-13",
    }
}

THE_KIND_EVOLVER_RECORDS_FOR_EVERY_VIDEO = {"video": {"type": "short"}}


_EXAMPLE_USERSCRIPT = (Path(__file__).resolve().parent.parent
                       / "fun_time" / "static" / "regen_autofill.example.user.js")


def test_the_example_userscript_names_every_key_of_the_payload():
    the_shape_it_reads = re.search(
        r"with shape:(.*?)\n \*\n", _EXAMPLE_USERSCRIPT.read_text(encoding="utf-8"), re.DOTALL
    ).group(1)
    keys_it_reads = set(re.findall(r"[a-z_]+(?=\s*[:,}])", the_shape_it_reads)) - {"label", "val"}

    for metadata in (IMAGE_META, VIDEO_ONLY_META):
        assert set(build_payload(metadata)) == keys_it_reads


def _decode(url: str) -> dict:
    return json.loads(unquote(url.split("#ft=", 1)[1]))


def test_payload_for_source_image_targets_image_with_both_prompts():
    payload = build_payload(IMAGE_META)

    assert payload["kind"] == "image"
    assert payload["positive"] == "POSITIVE"
    assert payload["negative"] == "NEGATIVE"
    assert payload["video_prompt"] == "VIDEO PROMPT"
    assert ["Quality", "Best"] in payload["settings"]
    assert ["Style", "Default"] in payload["settings"]
    assert ["Model", "Realism"] in payload["settings"]
    assert ["Action", "Oral Insertion"] in payload["video_settings"]


def test_payload_for_video_only_targets_video_no_negative():
    payload = build_payload(VIDEO_ONLY_META)

    assert payload["kind"] == "video"
    assert payload["positive"] == "JUST A VIDEO"
    assert payload["negative"] == ""
    assert payload["video_prompt"] == ""
    assert ["Action", "Side Epsilon"] in payload["settings"]


def test_payload_skips_empty_setting_values():
    meta = {"video": {"prompt": "p", "model": "", "seed": "123"}}
    labels = [pair[0] for pair in build_payload(meta)["settings"]]
    assert "Model" not in labels
    assert "Seed" in labels


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


def _ai_video(tmp_path: Path, name: str, *, source: str = "provider") -> Path:
    video = tmp_path / "videos" / "videos" / "kino" / "2D" / "AI" / "portrait" / source / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")
    return video


def _regen_url(video: Path, gallery: Gallery, metadata_root: Path | None = None) -> str:
    return regen_url_for_video(
        video, metadata_root=metadata_root, gallery=gallery, video_url=VIDEO_URL,
        image_url=IMAGE_URL, providers=_PROVIDERS)


def test_an_ai_video_regenerates_from_the_record_its_gallery_row_makes(tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4")
    gallery = Gallery([a_row("gen-1", "abc_00001_.mp4", recipe_category="Alpha", params={
        "positive_prompt": "MADE UP", "seed": 7, "model": "Made Up", "resolution": "544x816"})])

    url = _regen_url(video, gallery)

    assert url.startswith(VIDEO_URL + "#ft=")
    assert _decode(url) == {"kind": "video", "positive": "MADE UP", "negative": "",
                            "settings": [["Model", "Made Up"], ["Action", "Alpha"],
                                         ["Resolution", "544x816"], ["Seed", "7"]],
                            "video_prompt": "", "video_settings": []}


def test_an_ai_video_made_from_a_start_image_regenerates_from_that_image(tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4")
    gallery = Gallery(list(a_video_from_a_start_image(
        "abc_00001_.mp4", "frame one.png", video={"positive_prompt": "VIDEO PROMPT"},
        image={"positive_prompt": "POSITIVE", "negative_prompt": "NEGATIVE", "model": "Made Up"})))

    payload = _decode(_regen_url(video, gallery))

    assert (payload["kind"], payload["positive"], payload["negative"], payload["video_prompt"]) == (
        "image", "POSITIVE", "NEGATIVE", "VIDEO PROMPT")


def test_an_ai_video_no_provider_made_offers_no_regenerate_link(tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4", source="provider2")
    gallery = Gallery([a_row("gen-1", "abc_00001_.mp4", params={"positive_prompt": "MADE UP"})])

    assert _regen_url(video, gallery) == ""


def test_an_ai_video_whose_row_holds_no_prompt_offers_no_regenerate_link(tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4")

    assert _regen_url(video, Gallery([a_row("gen-1", "abc_00001_.mp4", params={"seed": 7})])) == ""


def test_an_ai_video_no_row_made_offers_no_regenerate_link(tmp_path: Path):
    assert _regen_url(_ai_video(tmp_path, "missing_topaz.mp4"), Gallery([])) == ""


def test_an_ai_videos_own_record_is_never_read_for_its_link(tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4")
    metadata_root = tmp_path / "videos" / "metadata"
    record = metadata_path_for(video, metadata_root)
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(VIDEO_ONLY_META), encoding="utf-8")

    assert _regen_url(video, Gallery([]), metadata_root) == ""


def test_a_video_outside_the_ai_folders_regenerates_from_its_own_record(tmp_path: Path):
    video = tmp_path / "videos" / "videos" / "kino" / "2D" / "non_AI" / "provider" / "abc_1.mp4"
    metadata_root = tmp_path / "videos" / "metadata"
    record = metadata_path_for(video, metadata_root)
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(IMAGE_META), encoding="utf-8")

    assert _regen_url(video, Gallery([]), metadata_root).startswith(IMAGE_URL + "#ft=")
