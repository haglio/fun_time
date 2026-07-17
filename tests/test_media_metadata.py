from __future__ import annotations

import json
from pathlib import Path

from fun_time.media_metadata import (
    action_group_key,
    action_group_members,
    action_label,
    build_group_index,
    seed_family_members,
    cached_group_index,
    load_metadata,
    loose_seed_group_key,
    matches_query,
    metadata_path_for,
    normalize_path_key,
    path_matches_query,
    reset_group_index_cache,
    search_haystack,
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

    result = metadata_path_for(video, metadata_root)

    assert result == metadata_root / "2D" / "AI" / "2_outbox" / "upscaled_by_orientation" / "portrait" / "provider" / "abc_topaz.json"


def test_metadata_path_returns_none_when_outside_media_root(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    outside = tmp_path / "elsewhere" / "clip.mp4"

    assert metadata_path_for(outside, metadata_root) is None


def test_metadata_path_returns_none_when_roots_missing(tmp_path: Path):
    assert metadata_path_for(tmp_path / "x.mp4", None) is None


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
    media_root, metadata_root, paths = _library(tmp_path, {
        "subject1_zeta": _i2v_meta(action="Zeta Massage", video_seed="1"),
        "subject1_alpha": _i2v_meta(action="Alpha", video_seed="2"),
        "subject2_alpha": _i2v_meta(action="Alpha", video_seed="3", image_overrides={"seed": "999"}),
        "no_metadata": None,
    })

    index = build_group_index(paths.values(), metadata_root)

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

    index = build_group_index(paths.values(), metadata_root)

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


# --- attribute filtering: haystack + query matching ------------------------

def test_search_haystack_combines_action_and_positive_prompts_lowercased():
    meta = {
        "video": {"action": "Beta Gamma", "prompt": "A Subject LAYING prone"},
        "source_image": {"positive_prompt": "redacted by the Pool"},
    }
    hay = search_haystack(meta)
    assert "beta gamma" in hay  # from action
    assert "laying prone" in hay  # from video prompt
    assert "redacted" in hay and "pool" in hay  # from positive_prompt


def test_search_haystack_excludes_the_negative_prompt():
    meta = {
        "video": {"action": "Alpha", "prompt": "x"},
        "source_image": {"positive_prompt": "y", "negative_prompt": "delta gamma"},
    }
    hay = search_haystack(meta)
    assert "alpha" in hay
    assert "delta" not in hay
    assert "gamma" not in hay


def test_search_haystack_tolerates_missing_blocks():
    assert search_haystack({}) == ""
    assert "epsilon" in search_haystack({"video": {"action": "Pov Epsilon"}})


def test_matches_query_is_case_insensitive_substring_across_fields():
    meta = {
        "video": {"action": "Pov Epsilon", "prompt": "redacted subject"},
        "source_image": {"positive_prompt": "pool party"},
    }
    assert matches_query(meta, "epsilon")  # substring of the action
    assert matches_query(meta, "Epsilon")  # case-insensitive
    assert matches_query(meta, "redacted")  # from the video prompt
    assert matches_query(meta, "pool")  # from the positive prompt
    assert not matches_query(meta, "delta")


def test_matches_query_empty_matches_everything():
    assert matches_query({}, "")
    assert matches_query({}, "   ")


def test_matches_query_multiword_must_be_contiguous():
    prone = {"video": {"action": "Beta Gamma", "prompt": "x"}}
    assert matches_query(prone, "beta gamma")
    scattered = {"video": {"action": "Alpha", "prompt": "she lies prone; a bone rests elsewhere"}}
    assert matches_query(scattered, "prone")
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
    assert path_matches_query(str(video), metadata_root, "")  # no filter passes all


def test_path_matches_query_excludes_videos_without_a_sidecar(tmp_path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    video = media_root / "portrait" / "other" / "no_meta.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"")

    # An active filter can't be satisfied by a video with no metadata...
    assert not path_matches_query(str(video), metadata_root, "alpha")
    # ...but with no filter, everything passes.
    assert path_matches_query(str(video), metadata_root, "")


# --- group membership for the action/seed loops ----------------------------

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


def test_action_group_members_are_the_subjects_other_actions(tmp_path: Path):
    media_root, metadata_root, paths = _write_library(tmp_path, {
        # Same prompt+seed => same subject; the action varies within the group.
        "clip": _t2v("Alpha", "1"),
        "kiss": _t2v("Kissing", "1"),
        # A different seed is a different subject.
        "other": _t2v("Alpha", "2"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    members = action_group_members(index, paths["clip"])

    assert sorted(members) == sorted([paths["clip"], paths["kiss"]])
    assert index.action_by_path[normalize_path_key(paths["clip"])] == "Alpha"


def test_seed_family_members_are_the_same_act_under_other_seeds(tmp_path: Path):
    media_root, metadata_root, paths = _write_library(tmp_path, {
        # Same prompt+action, different seed => same family (another subject).
        "clip_a": _t2v("Alpha", "1"),
        "clip_b": _t2v("Alpha", "2"),
        # Same subject, different act => not in the alpha seed family.
        "kiss": _t2v("Kissing", "1"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    members = seed_family_members(index, paths["clip_a"])

    assert sorted(members) == sorted([paths["clip_a"], paths["clip_b"]])
    assert paths["kiss"] not in members


def test_seed_family_members_pin_the_action_for_image_to_video(tmp_path: Path):
    """An i2v family is keyed on the source image, which does not pin the action,
    so members must be narrowed to the current clip's action."""
    def i2v(action: str, image_seed: str) -> dict:
        return {
            "video": {"prompt": f"do {action}", "action": action, "seed": "77"},
            "source_image": {"positive_prompt": "subject", "seed": image_seed},
        }

    media_root, metadata_root, paths = _write_library(tmp_path, {
        "clip_a": i2v("Alpha", "1"),
        "clip_b": i2v("Alpha", "2"),
        "kiss_b": i2v("Kissing", "2"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    members = seed_family_members(index, paths["clip_a"])

    assert sorted(members) == sorted([paths["clip_a"], paths["clip_b"]])
    assert paths["kiss_b"] not in members  # same family, wrong act


def test_widened_seed_members_add_same_act_clips_from_other_subjects(tmp_path: Path):
    """The widened seed row ("more seeds") is the exact family plus every other
    same-act clip, whatever its config — but never a different act."""
    from fun_time.media_metadata import widened_seed_members

    media_root, metadata_root, paths = _write_library(tmp_path, {
        "clip_a": _t2v("Alpha", "1"),                 # current
        "clip_b": _t2v("Alpha", "2"),                 # exact family (same prompt)
        "clip_other": _t2v("Alpha", "9", prompt="a different scene"),  # same act, other config
        "kiss": _t2v("Kissing", "1"),                  # different act — excluded
    })
    index = build_group_index(list(paths.values()), metadata_root)

    members = widened_seed_members(index, paths["clip_a"])

    assert set(members) >= {paths["clip_a"], paths["clip_b"], paths["clip_other"]}
    assert paths["kiss"] not in members


def test_action_label_numbers_duplicate_actions_in_a_group(tmp_path: Path):
    """Two Alphas of one seed are ordinary action-group siblings, read as
    "Alpha 1" and "Alpha 2"."""
    media_root, metadata_root, paths = _write_library(tmp_path, {
        "clip_one": _t2v("Alpha", "1"),
        "clip_two": _t2v("Alpha", "1"),
        "kiss": _t2v("Kissing", "1"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    # All three share a subject (same prompt+seed), so they cycle together.
    assert sorted(action_group_members(index, paths["clip_one"])) == sorted(paths.values())
    labels = {action_label(index, paths[name]) for name in ("clip_one", "clip_two")}
    assert labels == {"Alpha 1", "Alpha 2"}
    assert action_label(index, paths["kiss"]) == "Kissing"  # sole Kissing, unnumbered
