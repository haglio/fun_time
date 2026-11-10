from __future__ import annotations

import _winapi
import json
from pathlib import Path

import pytest

from fun_time.media_metadata import (
    EXCERPT,
    load_metadata,
    metadata_path_for,
    recorded_for,
    recorded_orientation,
    video_title,
    video_type_of,
)

SOURCE_IMAGE = {
    "positive_prompt": "two cute dolls, rainbow bedroom",
    "negative_prompt": "tan lines",
    "model": "X Sweet",
    "resolution": "1728x1344",
    "aspect_ratio": "9:7",
    "quality": "Best",
    "seed": "3092817138",
    "created": "2025-12-04",
    "style": "Default",
    "creativity": "7",
}


VIDEO_ONLY_META = {
    "video": {
        "prompt": "JUST A VIDEO",
        "model": "Video v3",
        "action": "Side Epsilon",
        "labels": ["Side Epsilon"],
        "resolution": "1280x720",
        "aspect_ratio": "16:9",
        "quality": "720p",
        "seed": "4029423637",
        "created": "2026-03-13",
    }
}


def test_metadata_path_mirrors_media_tree_under_metadata_root(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "upscales" / "portrait" / "provider" / "abc_topaz.mp4"

    result = metadata_path_for(video, metadata_root)

    assert result == metadata_root / "2D" / "AI" / "upscales" / "portrait" / "provider" / "abc_topaz.json"


def test_a_video_outside_the_library_has_no_metadata_file(tmp_path: Path):
    metadata_root = tmp_path / "videos" / "metadata"
    outside = tmp_path / "elsewhere" / "clip.mp4"

    assert metadata_path_for(outside, metadata_root) is None


def test_without_both_roots_configured_no_metadata_file_is_named(tmp_path: Path):
    assert metadata_path_for(tmp_path / "x.mp4", None) is None


def test_a_library_folder_kept_on_another_drive_mirrors_as_the_same_tree_would_beside_the_root(
        tmp_path: Path):
    metadata_root = tmp_path / "local" / "videos" / "metadata"
    away = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished"

    result = metadata_path_for(away / "scene one.mp4", metadata_root, outlying_dirs=[away])

    assert result == metadata_root / "VR" / "finished" / "scene one.json"


@pytest.mark.parametrize("flicks_folder", [
    Path("D:/suite/videos/genau/flicks"),
    Path("D:/suite/videos/videos/genau/flicks"),
], ids=["beside the video tree", "inside the video tree"])
def test_a_genau_flicks_record_is_filed_in_the_same_place_wherever_genaus_folder_sits(
        flicks_folder: Path):
    metadata_root = Path("D:/suite/videos/metadata")
    flick = flicks_folder / "VR" / "loop.mp4"
    filed = metadata_root / "genau" / "flicks" / "VR" / "loop.json"

    assert metadata_path_for(flick, metadata_root) == filed
    assert metadata_path_for(flick, metadata_root, outlying_dirs=[flicks_folder / "VR"]) == filed


def test_an_outlying_folder_inside_no_library_folder_is_measured_from_the_folder_holding_it(
        tmp_path: Path):
    metadata_root = tmp_path / "local" / "videos" / "metadata"
    away = tmp_path / "elsewhere" / "headset"

    result = metadata_path_for(away / "scene one.mp4", metadata_root, outlying_dirs=[away])

    assert result == metadata_root / "headset" / "scene one.json"


def test_metadata_path_places_a_lowercased_key_under_a_root_with_a_capital(tmp_path: Path):
    library = tmp_path / "Library"
    metadata_root = library / "metadata"
    video = library / "videos" / "2D" / "clip.mp4"

    sidecar = metadata_path_for(str(video).lower(), metadata_root)

    assert sidecar is not None
    assert str(sidecar).lower() == str(metadata_root / "2D" / "clip.json").lower()


def test_load_metadata_reads_dict(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(VIDEO_ONLY_META), encoding="utf-8")

    assert load_metadata(p) == VIDEO_ONLY_META


def test_a_missing_or_unreadable_sidecar_reads_as_no_metadata(tmp_path: Path):
    assert load_metadata(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_metadata(bad) == {}


def test_what_was_recorded_for_a_video_is_read_from_its_mirrored_file(tmp_path: Path):
    metadata_root = tmp_path / "videos" / "metadata"
    video = tmp_path / "videos" / "videos" / "2D" / "scene one.mp4"
    record = metadata_root / "2D" / "scene one.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(VIDEO_ONLY_META), encoding="utf-8")

    assert recorded_for(video, metadata_root) == VIDEO_ONLY_META
    assert recorded_for(video.with_name("scene two.mp4"), metadata_root) == {}
    assert recorded_for(tmp_path / "elsewhere" / "scene one.mp4", metadata_root) == {}
    assert recorded_for(video, None) == {}


def test_a_video_kept_on_another_drive_is_read_through_its_outlying_folder(tmp_path: Path):
    metadata_root = tmp_path / "local" / "videos" / "metadata"
    away = tmp_path / "cloud" / "videos" / "videos" / "VR"
    record = metadata_root / "VR" / "scene one.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps(VIDEO_ONLY_META), encoding="utf-8")

    assert recorded_for(away / "scene one.mp4", metadata_root, outlying_dirs=[away]) == VIDEO_ONLY_META


def test_a_video_under_the_library_is_mapped_without_asking_the_disk_where_it_really_is(tmp_path, monkeypatch):
    def never(self, strict=False):
        raise AssertionError(f"resolved {self}")

    monkeypatch.setattr(Path, "resolve", never)
    root = tmp_path / "videos"
    assert metadata_path_for(root / "videos" / "2D" / "clip.mp4", root / "metadata") == (
        root / "metadata" / "2D" / "clip.json")


def test_a_video_reached_through_a_junction_is_still_placed_under_the_library(tmp_path):
    root = tmp_path / "videos"
    (root / "videos" / "2D").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "videos" / "2D" / "clip.mp4").write_bytes(b"")
    link = tmp_path / "shortcut"
    _winapi.CreateJunction(str(root / "videos"), str(link))

    assert metadata_path_for(link / "2D" / "clip.mp4", root / "metadata") == (
        root / "metadata" / "2D" / "clip.json")


class TestWhatAVideoIsCalled:
    def test_the_recorded_name_is_what_the_video_is_called(self):
        assert video_title(
            {"title": "Jane Doe - Alpha Study: Part Two",
             "version": {"group": "Jane Doe - Alpha Study Part Two"}},
            "C:/lib/Jane Doe - Alpha Study Part Two_apo8.mp4",
        ) == "Jane Doe - Alpha Study: Part Two"

    def test_a_video_evolver_has_not_named_yet_is_called_by_its_own_record(self):
        assert video_title(
            {"clip": {"performer": "Jane Doe", "source": "Alpha Study 3"}},
            "C:/lib/whatever.mp4",
        ) == "Jane Doe - Alpha Study 3"

    def test_a_video_with_neither_falls_back_to_its_family_then_its_filename(self):
        assert video_title({"version": {"group": "Jane-Doe_540-hQ2vLm8t"}},
                           "C:/lib/Jane-Doe_540-hQ2vLm8t_apo8.mp4") == "Jane-Doe_540-hQ2vLm8t"
        assert video_title({}, "C:/lib/Jane-Doe_540-hQ2vLm8t.mp4") == "Jane-Doe_540-hQ2vLm8t"


def test_a_recorded_kind_is_the_kind_whatever_else_the_sidecar_holds():
    payload = {"video": {"type": "short"}, "clip": {"compilation": "Vol1"}}

    assert video_type_of(payload) == "short"


def test_a_sidecar_from_before_kinds_with_a_carved_clip_reads_as_an_excerpt():
    assert video_type_of({"clip": {"compilation": "Vol1", "index": 1}}) == EXCERPT


def test_a_sidecar_recording_no_kind_and_no_clip_has_no_kind():
    assert video_type_of({"video": {"action": "Alpha"}}) == ""


def test_a_video_with_no_recorded_orientation_is_landscape():
    assert recorded_orientation({}) == "landscape"


def test_an_orientation_nobody_knows_is_landscape_too():
    assert recorded_orientation({"video": {"orientation": "diagonal"}}) == "landscape"


def test_a_recorded_portrait_orientation_is_kept():
    assert recorded_orientation({"video": {"orientation": "portrait"}}) == "portrait"
