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
    matches_query,
    metadata_path_for,
    normalize_path_key,
    path_matches_query,
    reject_action,
    filter_haystack,
    seed_group_key,
    widened_seed_members,
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


def test_build_group_index_reads_each_clips_scene_tags(tmp_path: Path):
    """The widen ranks by prompt-tag overlap, so the index carries each clip's tag
    set — the image prompt's comma-separated phrases, normalized."""
    media_root, metadata_root, paths = _library(tmp_path, {
        "subject": _i2v_meta(action="Alpha", video_seed="1"),
        "no_metadata": None,
    })

    index = build_group_index(paths.values(), metadata_root)

    assert index.scene_tags_by_path[normalize_path_key(paths["subject"])] == frozenset(
        {"two cute dolls", "rainbow bedroom"}   # SOURCE_IMAGE's positive_prompt, split on commas
    )
    assert normalize_path_key(paths["no_metadata"]) not in index.scene_tags_by_path


# --- cached_group_index ---


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


# --- attribute filtering: haystack + query matching ------------------------

def test_filter_haystack_is_the_recorded_act_lowercased():
    meta = {
        "video": {"action": "Beta Gamma", "prompt": "A Subject LAYING prone"},
        "source_image": {"positive_prompt": "Subject by the Pool"},
    }
    assert filter_haystack(meta) == "beta gamma"


def test_filter_haystack_excludes_both_prompts():
    """A prompt says what was asked for, not what the clip shows: an act filter
    that read them handed back clips the HUD labels with some other act."""
    meta = {
        "video": {"action": "Alpha", "prompt": "delta by the pool"},
        "source_image": {"positive_prompt": "gamma", "negative_prompt": "epsilon"},
    }
    hay = filter_haystack(meta)
    assert hay == "alpha"
    assert "delta" not in hay  # the video prompt
    assert "gamma" not in hay  # the source image's positive prompt
    assert "epsilon" not in hay  # and the negative one, as ever


def test_filter_haystack_tolerates_missing_blocks():
    assert filter_haystack({}) == ""
    assert filter_haystack({"video": {}}) == ""
    assert filter_haystack({"video": {"prompt": "epsilon"}}) == ""
    assert "epsilon" in filter_haystack({"video": {"action": "Pov Epsilon"}})


def test_matches_query_is_a_case_insensitive_substring_of_the_act():
    meta = {
        "video": {"action": "Pov Epsilon", "prompt": "subject subject"},
        "source_image": {"positive_prompt": "pool party"},
    }
    assert matches_query(meta, "epsilon")  # substring of the action
    assert matches_query(meta, "Epsilon")  # case-insensitive
    assert not matches_query(meta, "subject")  # the video prompt is not the act
    assert not matches_query(meta, "pool")  # nor is the source image's
    assert not matches_query(meta, "delta")


def test_matches_query_drops_a_clip_that_has_no_act_recorded():
    """The clip a filter used to leave on screen labeled "(unknown)": its prompts
    named the act, its sidecar recorded none, so nothing in the map said why it
    was there.  Un-acted clips are the backfill tool's backlog, not filter hits."""
    unlabeled = {
        "video": {"prompt": "epsilon, by the pool"},
        "source_image": {"positive_prompt": "epsilon"},
    }
    assert not matches_query(unlabeled, "epsilon")
    assert matches_query(unlabeled, "")  # but no filter still passes it


def test_matches_query_empty_matches_everything():
    assert matches_query({}, "")
    assert matches_query({}, "   ")


def test_matches_query_multiword_must_be_contiguous():
    joined = {"video": {"action": "Beta Gamma", "prompt": "x"}}
    assert matches_query(joined, "beta gamma")
    assert matches_query(joined, "gamma")  # one word of the act still catches it
    scattered = {"video": {"action": "Beta, Theta Gamma", "prompt": "x"}}
    assert matches_query(scattered, "theta gamma")
    assert not matches_query(scattered, "beta gamma")  # two acts, not one


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


def test_path_matches_query_excludes_a_sidecar_that_records_no_act(tmp_path):
    """A sidecar whose prompts name the act but whose ``action`` is still empty:
    the filter used to keep it, and the HUD then labeled it "(unknown)"."""
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


def test_widened_seed_members_add_the_most_similar_clips_capped(tmp_path: Path):
    """"more seeds" adds the clips whose prompt is closest to this one — nearest
    first, and only a handful.  Exact-config sisters come along as always; a
    same-act clip sharing no prompt tags is not "more seeds", it is the rest of
    the library, so the cap keeps it out."""
    media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": _t2v("Alpha", "1", prompt="a, b, c, d"),
        "sister": _t2v("Alpha", "2", prompt="a, b, c, d"),   # exact family (same prompt)
        "near": _t2v("Alpha", "3", prompt="a, b, c, e"),     # 3 of 5 tags shared
        "far": _t2v("Alpha", "4", prompt="x, y, z"),         # nothing in common
    })
    index = build_group_index(list(paths.values()), metadata_root)

    members = widened_seed_members(index, paths["cur"], additions=1)

    assert sorted(members) == sorted([paths["cur"], paths["sister"], paths["near"]])
    assert paths["far"] not in members  # the cap stops at the nearest, not the whole act


def test_widened_seed_members_never_leave_the_clips_own_action(tmp_path: Path):
    """The seed axis is "the same act, another subject", so the action bounds the
    widen outright — a nearer-scened clip doing something else is not a wider seed
    row, it is the action column, and "more seeds" loops what it draws, so ranking
    that clip in put another act on screen."""
    media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": _t2v("Alpha", "1", prompt="a, b, c"),
        "other_act": _t2v("Theta", "2", prompt="a, b, c"),  # every tag shared
        "same_act": _t2v("Alpha", "3", prompt="a, x, y"),   # 1 of 5 shared, but the act
    })
    index = build_group_index(list(paths.values()), metadata_root)

    # Room for both: the other act is left out because it is the other act, not
    # because the cap ran out.
    assert widened_seed_members(index, paths["cur"], additions=6) == [
        paths["cur"], paths["same_act"],
    ]


def test_widened_seed_members_come_up_empty_on_a_one_of_a_kind_act(tmp_path: Path):
    """An act nothing else in the library does has no wider seed row.  That is a
    real answer — the caller's "widening net failed" notice — and the widen used to
    dodge it by handing back the nearest clip of some other act."""
    media_root, metadata_root, paths = _write_library(tmp_path, {
        "solo": _t2v("Zeta", "1", prompt="a, b, c"),   # the only Zeta
        "near": _t2v("Alpha", "2", prompt="a, b, c"),   # another act, same scene
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert widened_seed_members(index, paths["solo"], additions=6) == [paths["solo"]]


def test_widened_seed_members_read_one_act_spelled_two_ways_as_one_act(tmp_path: Path):
    """The library holds "POV …" beside "Pov …".  With the act now bounding the row
    rather than merely ranking it, a raw string compare would leave a clip alone in
    its casing with no seed row at all."""
    media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": _t2v("POV Alpha", "1", prompt="a, b, c"),
        "other_casing": _t2v("Pov Alpha", "2", prompt="a, b, c"),
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert widened_seed_members(index, paths["cur"]) == [paths["cur"], paths["other_casing"]]


def test_widened_seed_members_prefer_their_own_generation_kind(tmp_path: Path):
    """An image-to-video clip and a text-to-video one look drastically different
    even doing the same act, so within the action the widen ranks its own kind
    first — and falls through to the other kind rather than to nothing."""
    def i2v(action: str, seed: str, prompt: str) -> dict:
        return {
            "video": {"prompt": f"do {action}", "action": action, "seed": seed},
            "source_image": {"positive_prompt": prompt, "seed": seed},
        }

    media_root, metadata_root, paths = _write_library(tmp_path, {
        "cur": i2v("Delta", "1", "a, b, c"),
        "t2v_kin": _t2v("Delta", "2", prompt="a, b, c"),  # every tag shared — but t2v
        "i2v_kin": i2v("Delta", "3", "a, b, d"),          # own kind, a scene further off
    })
    index = build_group_index(list(paths.values()), metadata_root)

    assert widened_seed_members(index, paths["cur"], additions=1) == [
        paths["cur"], paths["i2v_kin"],
    ]
    # Only a preference: with no clip of its own kind, the other kind still comes.
    assert widened_seed_members(index, paths["t2v_kin"], additions=1)[1:] != []


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


def test_reject_action_strikes_the_act_and_remembers_it(tmp_path: Path):
    """Saying the act is wrong empties ``video.action`` — so the clip reads as
    unlabeled again — and keeps what it said under ``video.wrong_action``."""
    _media_root, metadata_root, paths = _write_library(tmp_path, {"clip": _t2v("Alpha", "1")})

    struck = reject_action(paths["clip"], metadata_root)

    assert struck == "Alpha"
    payload = load_metadata(metadata_path_for(paths["clip"], metadata_root))
    assert "action" not in payload["video"]
    assert payload["video"]["wrong_action"] == "Alpha"
    assert payload["video"]["prompt"], "the rest of the sidecar survives"


def test_reject_action_is_a_no_op_when_there_is_no_act(tmp_path: Path):
    """A clip that was never labeled has nothing to be wrong about, so its
    sidecar is left exactly as it was — and one with no sidecar at all is left
    without one."""
    _media_root, metadata_root, paths = _write_library(tmp_path, {"clip": _t2v("Alpha", "1")})
    sidecar = metadata_path_for(paths["clip"], metadata_root)
    sidecar.write_text(json.dumps({"video": {"prompt": "unlabeled"}}), encoding="utf-8")
    before = sidecar.read_text(encoding="utf-8")
    unknown = str(tmp_path / "videos" / "videos" / "never_seen.mp4")

    assert reject_action(paths["clip"], metadata_root) == ""
    assert reject_action(unknown, metadata_root) == ""

    assert sidecar.read_text(encoding="utf-8") == before
    assert not metadata_path_for(unknown, metadata_root).exists()


def test_the_satellite_hud_lights_a_row_for_exactly_the_clips_this_filter_keeps():
    """The lock HUD's lit filter button and this matcher must not drift apart.

    player_core owns the HUD now and carries its own table of the rule, but its
    suite runs with player_core alone on the path, so it cannot reach the
    authority it mirrors.  This side can: fun_time is the repo that wears the
    HUD and applies the filter, so the agreement is pinned here.  The empty
    query is the one deliberate difference — it keeps every clip, and lights no
    row.
    """
    from player_core.satellite_hud import label_is_filtered

    cases = [
        ("Gamma", "gamma", True),               # the row that names it
        ("POV Gamma", "gamma", True),           # the query is one act of the row
        ("Gamma, Theta", "gamma", True),        # one of two acts on the clip
        ("Gamma   Theta", "gamma theta", True),  # whitespace collapsed on both sides
        ("Gamma, Theta", "gamma, theta", True),  # the filter set from that very clip
        ("Gamma", "gamma, theta", False),       # …which does not keep a one-act clip
        ("Alpha", "gamma", False),
        ("Gam", "gamma", False),                # the label is not the longer query
    ]
    for label, query, expected in cases:
        assert matches_query({"video": {"action": label}}, query) is expected, (label, query)
        assert label_is_filtered(label, query) is expected, (label, query)

    assert matches_query({"video": {"action": "Gamma"}}, "") is True
    assert label_is_filtered("Gamma", "") is False
