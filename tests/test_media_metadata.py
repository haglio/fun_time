from __future__ import annotations

import json
from pathlib import Path

from fun_time.media_metadata import (
    action_group_key,
    build_group_index,
    cached_group_index,
    load_metadata,
    loose_seed_group_key,
    metadata_path_for,
    normalize_path_key,
    reset_group_index_cache,
    seed_group_key,
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


# --- action_group_key ---


def test_action_group_key_groups_videos_from_the_same_source_image():
    """Same exact source image = same subject(s) & situation, whatever the video step did."""
    zeta = _i2v_meta(action="Zeta Massage", video_seed="111")
    alpha = _i2v_meta(action="Alpha", video_seed="222")
    other_subject = _i2v_meta(action="Alpha", video_seed="222", image_overrides={"seed": "999"})

    assert action_group_key(zeta) == action_group_key(alpha)
    assert action_group_key(zeta) != action_group_key(other_subject)


def _t2v_meta(*, action: str, seed: str, prompt: str = "a subject on a beach") -> dict:
    return {
        "video": {
            "prompt": prompt,
            "model": "Video v3",
            "action": action,
            "resolution": "1280x720",
            "aspect_ratio": "16:9",
            "quality": "720p",
            "seed": seed,
            "created": "2026-03-13",
        }
    }


def test_action_group_key_for_text_to_video_frees_only_the_action():
    """No source image: the subject is pinned by the video prompt + seed instead."""
    dancing = _t2v_meta(action="Dancing", seed="42")
    kissing = _t2v_meta(action="Kissing", seed="42")
    other_subject = _t2v_meta(action="Dancing", seed="43")

    assert action_group_key(dancing) == action_group_key(kissing)
    assert action_group_key(dancing) != action_group_key(other_subject)
    assert action_group_key(dancing) != action_group_key(_i2v_meta(action="Dancing", video_seed="42"))


def test_action_group_key_returns_none_without_generation_identity():
    assert action_group_key({}) is None
    assert action_group_key({"video": {"action": "Alpha", "seed": "1"}}) is None


# --- seed_group_key ---


def test_seed_group_key_families_by_image_config_with_seed_free():
    """Same image prompt+settings with different seeds = same scenario, different subject."""
    subject_a = _i2v_meta(action="Alpha", video_seed="1")
    subject_b = _i2v_meta(action="Zeta Massage", video_seed="2", image_overrides={"seed": "999"})
    other_prompt = _i2v_meta(
        action="Alpha", video_seed="1", image_overrides={"positive_prompt": "elf queen"}
    )

    family_a, seed_a = seed_group_key(subject_a)
    family_b, seed_b = seed_group_key(subject_b)

    assert family_a == family_b
    assert seed_a != seed_b
    assert seed_group_key(other_prompt)[0] != family_a


def test_seed_group_key_for_text_to_video_keeps_action_in_the_family():
    """T2V configuration includes the action dropdown; only the seed is free."""
    subject_a = _t2v_meta(action="Dancing", seed="42")
    subject_b = _t2v_meta(action="Dancing", seed="43")
    different_action = _t2v_meta(action="Kissing", seed="44")

    assert seed_group_key(subject_a)[0] == seed_group_key(subject_b)[0]
    assert seed_group_key(subject_a)[0] != seed_group_key(different_action)[0]
    assert seed_group_key({"video": {"prompt": "x"}}) is None


# --- loose_seed_group_key ---


def test_loose_seed_group_key_frees_the_image_render_settings():
    """The loose family keeps the scene (prompts + style) but frees the render
    knobs, so a config differing only by an image quality setting is still kin."""
    subject_a = _i2v_meta(action="Alpha", video_seed="1")
    subject_b = _i2v_meta(
        action="Alpha", video_seed="2", image_overrides={"quality": "Draft", "seed": "999"}
    )

    assert seed_group_key(subject_a)[0] != seed_group_key(subject_b)[0]  # a render knob splits the strict family
    assert loose_seed_group_key(subject_a)[0] == loose_seed_group_key(subject_b)[0]  # loose reunites them
    assert loose_seed_group_key(subject_a)[1] != loose_seed_group_key(subject_b)[1]  # ...still different seeds


def test_loose_seed_group_key_keeps_the_text_to_video_action():
    """Freeing render knobs must not merge across actions — that is cycle-action's
    job. A quality-only difference is kin; a different action is not."""
    dancing = _t2v_meta(action="Dancing", seed="42")
    dancing_hi = {"video": {**dancing["video"], "quality": "1080p", "seed": "43"}}
    kissing = _t2v_meta(action="Kissing", seed="44")

    assert loose_seed_group_key(dancing)[0] == loose_seed_group_key(dancing_hi)[0]  # render knob freed
    assert loose_seed_group_key(dancing)[0] != loose_seed_group_key(kissing)[0]  # action still held


# --- build_group_index ---


def _library(tmp_path: Path, videos: dict[str, dict | None]) -> tuple[Path, Path, dict[str, str]]:
    """Write videos (+ optional sidecars) into a media/metadata tree pair."""
    media_root = tmp_path / "media"
    metadata_root = tmp_path / "metadata"
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        video = media_root / "portrait" / "provider" / f"{name}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
        if meta is not None:
            sidecar = metadata_path_for(video, media_root, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(meta), encoding="utf-8")
    return media_root, metadata_root, paths


def test_build_group_index_groups_by_action_and_seed_and_skips_sidecarless(tmp_path: Path):
    media_root, metadata_root, paths = _library(tmp_path, {
        "subject1_zeta": _i2v_meta(action="Zeta Massage", video_seed="1"),
        "subject1_alpha": _i2v_meta(action="Alpha", video_seed="2"),
        "subject2_alpha": _i2v_meta(action="Alpha", video_seed="3", image_overrides={"seed": "999"}),
        "no_metadata": None,
    })

    index = build_group_index(paths.values(), media_root, metadata_root)

    subject1_key = index.action_key_by_path[normalize_path_key(paths["subject1_zeta"])]
    assert sorted(index.action_members[subject1_key]) == sorted(
        [paths["subject1_zeta"], paths["subject1_alpha"]]
    )
    family, seed = index.seed_key_by_path[normalize_path_key(paths["subject1_alpha"])]
    assert set(index.seed_members[family]) == {
        paths["subject1_zeta"], paths["subject1_alpha"], paths["subject2_alpha"]
    }
    assert seed != index.seed_key_by_path[normalize_path_key(paths["subject2_alpha"])][1]
    assert normalize_path_key(paths["no_metadata"]) not in index.action_key_by_path
    assert index.contains(paths["no_metadata"])
    assert not index.contains(str(tmp_path / "media" / "new_arrival.mp4"))


def test_build_group_index_also_families_loosely_across_render_settings(tmp_path: Path):
    """Two clips of the same scene that differ only by a render knob split into
    separate strict families but share one loose family."""
    media_root, metadata_root, paths = _library(tmp_path, {
        "subject_best": _i2v_meta(action="Alpha", video_seed="1"),
        "subject_draft": _i2v_meta(
            action="Alpha", video_seed="2", image_overrides={"quality": "Draft", "seed": "999"}
        ),
    })

    index = build_group_index(paths.values(), media_root, metadata_root)

    best, draft = normalize_path_key(paths["subject_best"]), normalize_path_key(paths["subject_draft"])
    assert index.seed_key_by_path[best][0] != index.seed_key_by_path[draft][0]
    loose_family = index.loose_seed_key_by_path[best][0]
    assert index.loose_seed_key_by_path[draft][0] == loose_family
    assert set(index.loose_seed_members[loose_family]) == {paths["subject_best"], paths["subject_draft"]}


# --- cached_group_index ---


def test_cached_group_index_rescans_only_when_probe_path_is_unknown(tmp_path: Path):
    reset_group_index_cache()
    media_root, metadata_root, paths = _library(tmp_path, {
        "known": _i2v_meta(action="Alpha", video_seed="1"),
    })
    scans: list[int] = []

    def supplier() -> list[str]:
        scans.append(1)
        return list(paths.values())

    first = cached_group_index(
        "portrait", paths_supplier=supplier, media_root=media_root, metadata_root=metadata_root,
        must_contain=paths["known"],
    )
    second = cached_group_index(
        "portrait", paths_supplier=supplier, media_root=media_root, metadata_root=metadata_root,
        must_contain=paths["known"],
    )
    assert first is second
    assert len(scans) == 1

    new_arrival = str(media_root / "portrait" / "provider" / "fresh.mp4")
    cached_group_index(
        "portrait", paths_supplier=supplier, media_root=media_root, metadata_root=metadata_root,
        must_contain=new_arrival,
    )
    assert len(scans) == 2
