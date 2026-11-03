from __future__ import annotations

import _winapi
import json
import os
import threading
from pathlib import Path

import pytest
from app_support.json_store import locked_update
from funestra.satellite_hud import label_is_filtered

from fun_time.media_metadata import (
    EXCERPT,
    GroupIndexCache,
    action_group_items,
    action_label,
    build_group_index,
    cached_group_index,
    filter_haystack,
    load_metadata,
    matches_query,
    metadata_path_for,
    path_matches_query,
    recorded_for,
    recorded_orientation,
    reject_action,
    seed_family_items,
    video_title,
    video_type_of,
    watch_weight_of,
    widened_seed_items,
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


def _i2v_meta(*, action: str, video_seed: str, image_overrides: dict | None = None) -> dict:
    source = dict(SOURCE_IMAGE)
    if image_overrides:
        source.update(image_overrides)
    return {
        "video": {
            "prompt": f"prompt for {action}",
            "model": "Realism",
            "action": action,
            "resolution": "720x560",
            "aspect_ratio": "9:7",
            "quality": "720p",
            "seed": video_seed,
            "created": "2025-12-05",
        },
        "source_image": source,
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


def test_metadata_path_mirrors_media_tree_under_metadata_root(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider" / "abc_topaz.mp4"

    result = metadata_path_for(video, metadata_root)

    assert result == metadata_root / "2D" / "AI" / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider" / "abc_topaz.json"


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


def _library(tmp_path: Path, videos: dict[str, dict | None]) -> tuple[Path, Path, dict[str, str]]:
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        video = media_root / "portrait" / "provider" / f"{name}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
        if meta is not None:
            sidecar = metadata_path_for(video, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(meta), encoding="utf-8")
    return media_root, metadata_root, paths


def test_build_group_index_groups_by_action_and_seed_and_skips_sidecarless(tmp_path: Path):
    _media_root, metadata_root, paths = _library(tmp_path, {
        "subject1_zeta": _i2v_meta(action="Zeta Massage", video_seed="1"),
        "subject1_alpha": _i2v_meta(action="Alpha", video_seed="2"),
        "subject2_alpha": _i2v_meta(action="Alpha", video_seed="3", image_overrides={"seed": "999"}),
        "no_metadata": None,
    })

    index = build_group_index(paths.values(), metadata_root)

    subject1_key = index.entry(paths["subject1_zeta"]).action_key
    assert sorted(index.action_items[subject1_key]) == sorted(
        [paths["subject1_zeta"], paths["subject1_alpha"]]
    )
    family, seed = index.entry(paths["subject1_alpha"]).seed_key
    assert set(index.seed_items[family]) == {
        paths["subject1_zeta"], paths["subject1_alpha"], paths["subject2_alpha"]
    }
    assert seed != index.entry(paths["subject2_alpha"]).seed_key[1]
    assert index.entry(paths["no_metadata"]).action_key is None
    assert index.contains(paths["no_metadata"])
    assert not index.contains(str(tmp_path / "media" / "new_arrival.mp4"))


def test_the_index_holds_one_record_per_video(tmp_path: Path):
    _media_root, metadata_root, paths = _library(tmp_path, {
        "subject": {**_i2v_meta(action="Alpha", video_seed="1"), "watch": {"weight": 4.0}},
        "no_metadata": None,
    })

    index = build_group_index(paths.values(), metadata_root)

    entry = index.entry(paths["subject"])
    assert entry.path == paths["subject"]
    assert entry.action == "Alpha"
    assert entry.weight == 4.0
    assert entry.from_image
    assert entry.action_key is not None
    assert entry.seed_key is not None
    bare = index.entry(paths["no_metadata"])
    assert (bare.path, bare.action, bare.weight, bare.from_image) == (
        paths["no_metadata"], "", 1.0, False
    )
    assert index.entry("C:/never/indexed.mp4").path == ""


def test_build_group_index_carries_each_videos_watch_weight(tmp_path: Path):
    _media_root, metadata_root, paths = _library(tmp_path, {
        "loved": {**_i2v_meta(action="Alpha", video_seed="1"), "watch": {"weight": 4.0}},
        "plain": _i2v_meta(action="Alpha", video_seed="2"),
        "no_metadata": None,
    })

    index = build_group_index(paths.values(), metadata_root)

    assert index.weight_of(paths["loved"]) == 4.0
    assert index.weight_of(paths["plain"]) == 1.0
    assert index.weight_of(paths["no_metadata"]) == 1.0


def test_watch_weight_of_reads_the_stamped_weight_and_defaults_to_one():
    assert watch_weight_of({"watch": {"completions": 3, "skips": 0, "locks": 0, "weight": 2.0}}) == 2.0
    assert watch_weight_of({"video": {"type": "short"}}) == 1.0
    assert watch_weight_of({"watch": "eight"}) == 1.0
    assert watch_weight_of({"watch": {"weight": "heavy"}}) == 1.0


def test_build_group_index_reads_each_videos_scene_tags(tmp_path: Path):
    _media_root, metadata_root, paths = _library(tmp_path, {
        "subject": _i2v_meta(action="Alpha", video_seed="1"),
        "no_metadata": None,
    })

    index = build_group_index(paths.values(), metadata_root)

    assert index.entry(paths["subject"]).scene_tags == frozenset(
        {"two cute dolls", "rainbow bedroom"}
    )
    assert index.entry(paths["no_metadata"]).scene_tags == frozenset()


def test_cached_group_index_rescans_only_when_probe_path_is_unknown(tmp_path: Path):
    media_root, metadata_root, paths = _library(tmp_path, {
        "known": _i2v_meta(action="Alpha", video_seed="1"),
    })
    scans: list[int] = []

    def supplier() -> list[str]:
        scans.append(1)
        return list(paths.values())

    first = cached_group_index(
        "portrait", paths_supplier=supplier, metadata_root=metadata_root,
        must_contain=paths["known"],
    )
    second = cached_group_index(
        "portrait", paths_supplier=supplier, metadata_root=metadata_root,
        must_contain=paths["known"],
    )
    assert first is second
    assert len(scans) == 1

    new_arrival = str(media_root / "portrait" / "provider" / "fresh.mp4")
    cached_group_index(
        "portrait", paths_supplier=supplier, metadata_root=metadata_root,
        must_contain=new_arrival,
    )
    assert len(scans) == 2


def test_a_second_cache_holds_its_own_indexes(tmp_path: Path):
    _media_root, metadata_root, paths = _library(tmp_path, {
        "known": _i2v_meta(action="Alpha", video_seed="1"),
    })
    mine, yours = GroupIndexCache(), GroupIndexCache()

    ours = [
        cache.index_for(
            "portrait", paths_supplier=lambda: list(paths.values()),
            metadata_root=metadata_root,
        )
        for cache in (mine, yours)
    ]

    assert ours[0] is not ours[1]
    assert ours[0] is mine.index_for(
        "portrait", paths_supplier=list, metadata_root=metadata_root)
    mine.clear()
    assert ours[1] is yours.index_for(
        "portrait", paths_supplier=list, metadata_root=metadata_root)


def test_a_video_learned_after_the_build_stands_in_its_groups_as_if_built_in(tmp_path: Path):
    _media_root, metadata_root, paths = _library(tmp_path, {
        "subject1_zeta": _i2v_meta(action="Zeta Massage", video_seed="1"),
        "subject1_alpha": _i2v_meta(action="Alpha", video_seed="2"),
        "subject2_alpha": _i2v_meta(action="Alpha", video_seed="3", image_overrides={"seed": "9"}),
    })
    index = build_group_index([paths["subject1_zeta"], paths["subject2_alpha"]], metadata_root)

    index.learn(paths["subject1_alpha"], metadata_root)

    assert index == build_group_index(paths.values(), metadata_root)


def test_a_cache_learns_a_video_into_the_index_it_holds_for_its_library_without_a_rescan(
        tmp_path: Path):
    _media_root, metadata_root, paths = _library(tmp_path, {
        "known": _i2v_meta(action="Alpha", video_seed="1"),
        "arrived": _i2v_meta(action="Zeta Massage", video_seed="2"),
    })
    cache, scans = GroupIndexCache(), []

    def supplier() -> list[str]:
        scans.append(1)
        return [paths["known"]]

    held = cache.index_for("portrait", paths_supplier=supplier, metadata_root=metadata_root)
    cache.learn("portrait", paths["arrived"], metadata_root)
    cache.learn("landscape", paths["arrived"], metadata_root)

    assert held.contains(paths["arrived"])
    assert cache.index_for("portrait", paths_supplier=supplier, metadata_root=metadata_root,
                           must_contain=paths["arrived"]) is held
    assert len(scans) == 1
    assert cache.index_for("landscape", paths_supplier=list,
                           metadata_root=metadata_root).entries == {}


def test_filter_haystack_is_the_recorded_act_lowercased():
    meta = {
        "video": {"action": "Beta Gamma", "prompt": "A Subject LAYING prone"},
        "source_image": {"positive_prompt": "Subject by the Pool"},
    }
    assert filter_haystack(meta) == "beta gamma"


def test_filter_haystack_excludes_both_prompts():
    meta = {
        "video": {"action": "Alpha", "prompt": "delta by the pool"},
        "source_image": {"positive_prompt": "gamma", "negative_prompt": "epsilon"},
    }
    hay = filter_haystack(meta)
    assert hay == "alpha"
    assert "delta" not in hay
    assert "gamma" not in hay
    assert "epsilon" not in hay


def test_filter_haystack_tolerates_missing_blocks():
    assert filter_haystack({}) == ""
    assert filter_haystack({"video": {}}) == ""
    assert filter_haystack({"video": {"prompt": "epsilon"}}) == ""
    assert "epsilon" in filter_haystack({"video": {"action": "Side Epsilon"}})


def test_matches_query_is_a_case_insensitive_substring_of_the_act():
    meta = {
        "video": {"action": "Side Epsilon", "prompt": "subject subject"},
        "source_image": {"positive_prompt": "pool party"},
    }
    assert matches_query(meta, "epsilon")
    assert matches_query(meta, "Epsilon")
    assert not matches_query(meta, "subject")
    assert not matches_query(meta, "pool")
    assert not matches_query(meta, "delta")


def test_matches_query_drops_a_video_that_has_no_act_recorded():
    unlabeled = {
        "video": {"prompt": "epsilon, by the pool"},
        "source_image": {"positive_prompt": "epsilon"},
    }
    assert not matches_query(unlabeled, "epsilon")
    assert matches_query(unlabeled, "")


def test_matches_query_empty_matches_everything():
    assert matches_query({}, "")
    assert matches_query({}, "   ")


def test_matches_query_multiword_must_be_contiguous():
    joined = {"video": {"action": "Beta Gamma", "prompt": "x"}}
    assert matches_query(joined, "beta gamma")
    assert matches_query(joined, "gamma")
    scattered = {"video": {"action": "Beta, Theta Gamma", "prompt": "x"}}
    assert matches_query(scattered, "theta gamma")
    assert not matches_query(scattered, "beta gamma")


def test_path_matches_query_reads_the_sidecar(tmp_path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "portrait" / "provider" / "clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"")
    sidecar = metadata_root / "portrait" / "provider" / "clip.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"video": {"action": "Beta Gamma", "prompt": "x"}}), encoding="utf-8")

    assert path_matches_query(str(video), metadata_root, "beta gamma")
    assert not path_matches_query(str(video), metadata_root, "alpha")
    assert path_matches_query(str(video), metadata_root, "")


def test_path_matches_query_excludes_videos_without_a_sidecar(tmp_path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "portrait" / "other" / "no_meta.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"")

    assert not path_matches_query(str(video), metadata_root, "alpha")
    assert path_matches_query(str(video), metadata_root, "")


def test_path_matches_query_excludes_a_sidecar_that_records_no_act(tmp_path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "portrait" / "provider" / "unlabeled.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"")
    sidecar = metadata_root / "portrait" / "provider" / "unlabeled.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        json.dumps({
            "video": {"prompt": "alpha, by the pool"},
            "source_image": {"positive_prompt": "alpha"},
        }),
        encoding="utf-8",
    )

    assert not path_matches_query(str(video), metadata_root, "alpha")
    assert path_matches_query(str(video), metadata_root, "")


def _t2v(action: str, seed: str, prompt: str = "scene") -> dict:
    return {"video": {"prompt": prompt, "action": action, "seed": seed}}


def _write_library(tmp_path, videos: dict[str, dict]) -> tuple[Path, Path, dict[str, str]]:
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        video = media_root / f"{name}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_text("x", encoding="utf-8")
        sidecar = metadata_path_for(video, metadata_root)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
        paths[name] = str(video)
    return media_root, metadata_root, paths


def test_action_group_items_are_the_subjects_other_actions(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "clip": _t2v("Alpha", "1"),
        "twirl": _t2v("Twirling", "1"),
        "other": _t2v("Alpha", "2"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    items = action_group_items(index, paths["clip"])

    assert sorted(items) == sorted([paths["clip"], paths["twirl"]])
    assert index.act_of(paths["clip"]) == "Alpha"


def test_seed_family_items_are_the_same_act_under_other_seeds(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "clip_a": _t2v("Alpha", "1"),
        "clip_b": _t2v("Alpha", "2"),
        "twirl": _t2v("Twirling", "1"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    items = seed_family_items(index, paths["clip_a"])

    assert sorted(items) == sorted([paths["clip_a"], paths["clip_b"]])
    assert paths["twirl"] not in items


def test_seed_family_items_pin_the_action_for_image_to_video(tmp_path: Path):
    def i2v(action: str, image_seed: str) -> dict:
        return {
            "video": {"prompt": f"do {action}", "action": action, "seed": "77"},
            "source_image": {"positive_prompt": "subject", "seed": image_seed},
        }

    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "clip_a": i2v("Alpha", "1"),
        "clip_b": i2v("Alpha", "2"),
        "twirl_b": i2v("Twirling", "2"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    items = seed_family_items(index, paths["clip_a"])

    assert sorted(items) == sorted([paths["clip_a"], paths["clip_b"]])
    assert paths["twirl_b"] not in items


def test_widened_seed_items_add_the_most_similar_videos_capped(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": _t2v("Alpha", "1", prompt="a, b, c, d"),
        "sister": _t2v("Alpha", "2", prompt="a, b, c, d"),
        "near": _t2v("Alpha", "3", prompt="a, b, c, e"),
        "far": _t2v("Alpha", "4", prompt="x, y, z"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    items = widened_seed_items(index, paths["cur"], additions=1)

    assert sorted(items) == sorted([paths["cur"], paths["sister"], paths["near"]])
    assert paths["far"] not in items


def test_widened_seed_items_never_leave_the_videos_own_action(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": _t2v("Alpha", "1", prompt="a, b, c"),
        "other_act": _t2v("Theta", "2", prompt="a, b, c"),
        "same_act": _t2v("Alpha", "3", prompt="a, x, y"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert widened_seed_items(index, paths["cur"], additions=6) == [
        paths["cur"], paths["same_act"],
    ]


def test_widened_seed_items_come_up_empty_on_a_one_of_a_kind_act(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "solo": _t2v("Zeta", "1", prompt="a, b, c"),
        "near": _t2v("Alpha", "2", prompt="a, b, c"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert widened_seed_items(index, paths["solo"], additions=6) == [paths["solo"]]


def test_widened_seed_items_read_one_act_spelled_two_ways_as_one_act(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": _t2v("XYZ Alpha", "1", prompt="a, b, c"),
        "other_casing": _t2v("Xyz Alpha", "2", prompt="a, b, c"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert widened_seed_items(index, paths["cur"]) == [paths["cur"], paths["other_casing"]]


def test_widened_seed_items_prefer_their_own_generation_kind(tmp_path: Path):
    def i2v(action: str, seed: str, prompt: str) -> dict:
        return {
            "video": {"prompt": f"do {action}", "action": action, "seed": seed},
            "source_image": {"positive_prompt": prompt, "seed": seed},
        }

    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": i2v("Delta", "1", "a, b, c"),
        "t2v_kin": _t2v("Delta", "2", prompt="a, b, c"),
        "i2v_kin": i2v("Delta", "3", "a, b, d"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert widened_seed_items(index, paths["cur"], additions=1) == [
        paths["cur"], paths["i2v_kin"],
    ]
    assert widened_seed_items(index, paths["t2v_kin"], additions=1)[1:] != []


def test_action_label_numbers_duplicate_actions_in_a_group(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {
        "clip_one": _t2v("Alpha", "1"),
        "clip_two": _t2v("Alpha", "1"),
        "twirl": _t2v("Twirling", "1"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert sorted(action_group_items(index, paths["clip_one"])) == sorted(paths.values())
    labels = {action_label(index, paths[name]) for name in ("clip_one", "clip_two")}
    assert labels == {"Alpha 1", "Alpha 2"}
    assert action_label(index, paths["twirl"]) == "Twirling"


def test_reject_action_strikes_the_act_and_remembers_it(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {"clip": _t2v("Alpha", "1")})

    struck = reject_action(paths["clip"], metadata_root)

    assert struck == "Alpha"
    payload = load_metadata(metadata_path_for(paths["clip"], metadata_root))
    assert "action" not in payload["video"]
    assert payload["video"]["wrong_action"] == "Alpha"
    assert payload["video"]["prompt"], "the rest of the sidecar survives"


def test_reject_action_replaces_the_sidecar_whole(tmp_path: Path, monkeypatch):
    _media_root, metadata_root, paths = _write_library(tmp_path, {"clip": _t2v("Alpha", "1")})
    sidecar = metadata_path_for(paths["clip"], metadata_root)
    before = sidecar.read_text(encoding="utf-8")

    def refuse(_src, _dst):
        raise OSError("the rename was refused")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="could not replace"):
        reject_action(paths["clip"], metadata_root)
    assert sidecar.read_text(encoding="utf-8") == before

    monkeypatch.undo()
    assert reject_action(paths["clip"], metadata_root) == "Alpha"
    assert [p.name for p in sidecar.parent.iterdir()] == [sidecar.name]


def test_reject_action_waits_for_evolvers_writer_and_strikes_what_it_left(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {"clip": _t2v("Alpha", "1")})
    sidecar = metadata_path_for(paths["clip"], metadata_root)
    inside = threading.Event()
    let_go = threading.Event()

    def stamp_a_watch(payload: dict) -> dict:
        inside.set()
        let_go.wait(5)
        return {**payload, "watch": {"weight": 2.0}}

    pipeline = threading.Thread(
        target=locked_update, args=(sidecar, stamp_a_watch), daemon=True)
    pipeline.start()
    assert inside.wait(5)
    struck: list[str] = []
    viewer = threading.Thread(
        target=lambda: struck.append(reject_action(paths["clip"], metadata_root)),
        daemon=True)
    viewer.start()
    let_go.set()
    pipeline.join(5)
    viewer.join(5)

    payload = load_metadata(sidecar)
    assert struck == ["Alpha"]
    assert payload["video"]["wrong_action"] == "Alpha"
    assert "action" not in payload["video"]
    assert payload["watch"] == {"weight": 2.0}, "the pass's stamp survives the rejection"


def test_reject_action_leaves_a_sidecar_it_cannot_read_as_a_document(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {"clip": _t2v("Alpha", "1")})
    sidecar = metadata_path_for(paths["clip"], metadata_root)
    sidecar.write_text("[not a document]", encoding="utf-8")

    assert reject_action(paths["clip"], metadata_root) == ""
    assert sidecar.read_text(encoding="utf-8") == "[not a document]"


def test_reject_action_is_a_no_op_when_there_is_no_act(tmp_path: Path):
    _media_root, metadata_root, paths = _write_library(tmp_path, {"clip": _t2v("Alpha", "1")})
    sidecar = metadata_path_for(paths["clip"], metadata_root)
    sidecar.write_text(json.dumps({"video": {"prompt": "unlabeled"}}), encoding="utf-8")
    before = sidecar.read_text(encoding="utf-8")
    unknown = str(tmp_path / "videos" / "videos" / "never_seen.mp4")

    assert reject_action(paths["clip"], metadata_root) == ""
    assert reject_action(unknown, metadata_root) == ""

    assert sidecar.read_text(encoding="utf-8") == before
    assert not metadata_path_for(unknown, metadata_root).exists()


def test_the_satellite_hud_lights_a_row_for_exactly_the_videos_this_filter_keeps():
    camera_words = ("Side", "XYZ")
    cases = [
        ("Gamma", "gamma", True),
        ("Side Gamma", "gamma", True),
        ("Side Gamma", "side gamma", True),
        ("XYZ Gamma", "side gamma", False),
        ("Gamma, Theta", "gamma", True),
        ("Gamma   Theta", "gamma theta", True),
        ("Gamma, Theta", "gamma, theta", True),
        ("Gamma", "gamma, theta", False),
        ("Alpha", "gamma", False),
        ("Gam", "gamma", False),
    ]
    for label, query, expected in cases:
        assert matches_query({"video": {"action": label}}, query) is expected, (label, query)
        assert label_is_filtered(label, query, camera_words) is expected, (label, query)

    assert matches_query({"video": {"action": "Gamma"}}, "") is True
    assert label_is_filtered("Gamma", "", camera_words) is False


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
