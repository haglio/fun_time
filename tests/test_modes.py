from __future__ import annotations

import os
import random
from pathlib import Path

import json

from fun_time.modes import (
    SatelliteLibraryContext,
    build_all_playlists,
    build_mirrored_funscript_path,
    build_main_playlist_paths,
    build_satellite_playlist_paths,
    build_satellite_playlists,
    collect_video_files,
    has_matching_funscript,
    is_favorite_path,
    order_paths,
    read_favs_content,
    shuffle_paths,
    sort_paths_by_recency,
)
from fun_time.media_metadata import metadata_path_for


def _lines(playlist: Path) -> list[str]:
    """The lines of a playlist file a build just wrote."""
    return playlist.read_text(encoding="utf-8").splitlines()


def _touch_with_mtime(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _i2v_meta(image_seed: str, action: str) -> dict:
    return {
        "video": {"prompt": f"do {action}", "action": action, "seed": "77"},
        "source_image": {
            "positive_prompt": "cute subject, cafe",
            "negative_prompt": "bad",
            "seed": image_seed,
        },
    }


def _grouped_library(tmp_path: Path, videos: dict[str, dict | None]) -> tuple[Path, SatelliteLibraryContext, dict[str, str]]:
    """A satellite source dir whose videos (optionally) carry sidecars."""
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    source_dir = media_root / "portrait"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        video = source_dir / f"{name}.mp4"
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
        if meta is not None:
            sidecar = metadata_path_for(video, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(meta), encoding="utf-8")
    library = SatelliteLibraryContext(
        metadata_root=metadata_root,
        watch_stats_file=tmp_path / "state" / "watch_stats.json",
    )
    return source_dir, library, paths


def test_build_mirrored_funscript_path_uses_video_path_directly(tmp_path: Path):
    video_path = tmp_path / "videos" / "videos" / "primary" / "folder" / "clip.mp4"

    result = build_mirrored_funscript_path(str(video_path))

    assert result == str(tmp_path / "videos" / "scripts" / "scripts" / "primary" / "folder" / "clip.funscript")


def test_build_primary_playlist_paths_filters_to_funscripted_items_in_f_mode(tmp_path: Path):
    source_root = tmp_path / "videos" / "videos" / "primary"
    first = source_root / "a.mp4"
    second = source_root / "b.mp4"
    first.parent.mkdir(parents=True)
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")
    mirrored = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "a.funscript"
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    mirrored.write_text("{}", encoding="utf-8")

    paths = build_main_playlist_paths(str(source_root), True, rng=random.Random(1))

    assert paths == [str(first)]


def test_build_satellite_playlist_paths_filters_to_favorites_in_f_mode(tmp_path: Path):
    source_root = tmp_path / "portrait"
    first = source_root / "keep.mp4"
    second = source_root / "skip.mp4"
    source_root.mkdir(parents=True)
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text(f'local_file,web_url\r\n"x","{first}"\r\n', encoding="utf-8")

    paths = build_satellite_playlist_paths(str(source_root), True, favs_file, rng=random.Random(1))

    assert paths == [str(first)]


def test_build_all_playlists_writes_satellite_playlist_files(tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    for root in (primary_root, portrait_root, landscape_root):
        root.mkdir(parents=True)
    main_video = primary_root / "main.mp4"
    portrait_video = portrait_root / "portrait.mp4"
    landscape_video = landscape_root / "landscape.mp4"
    for path in (main_video, portrait_video, landscape_video):
        path.write_text("x", encoding="utf-8")
    mirrored = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "main.funscript"
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    mirrored.write_text("{}", encoding="utf-8")
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text(
        f'local_file,web_url\r\n"x","{portrait_video}"\r\n"x","{landscape_video}"\r\n',
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"

    build_all_playlists(
        main_sources=str(primary_root),
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=favs_file,
        state_dir=state_dir,
        main_f_mode=True,
        portrait_f_mode=True,
        landscape_f_mode=True,
        rng=random.Random(1),
    )

    # Each satellite gets a plain one-path-per-line playlist the native player
    # reads; the main slot is Nau, which reads its own .tsv playlist.  One
    # video from each source survives the favourites filter.
    assert _lines(state_dir / "portrait_playlist.tsv") == [str(portrait_video)]
    assert _lines(state_dir / "landscape_playlist.tsv") == [str(landscape_video)]
    assert _lines(state_dir / "nau_playlist.tsv") == [f"{main_video}\t{mirrored}"]
    assert not (state_dir / "main_playlist.tsv").exists()


def test_build_all_playlists_writes_nau_playlist_with_funscript_pairs(tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    primary_root.mkdir(parents=True)
    scripted_video = primary_root / "scripted.mp4"
    plain_video = primary_root / "plain.mp4"
    scripted_video.write_text("x", encoding="utf-8")
    plain_video.write_text("x", encoding="utf-8")
    mirrored = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "scripted.funscript"
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    mirrored.write_text("{}", encoding="utf-8")
    favs_file = tmp_path / "favs.csv"
    state_dir = tmp_path / "state"

    build_all_playlists(
        main_sources=str(primary_root),
        portrait_sources="",
        landscape_sources="",
        favs_file=favs_file,
        state_dir=state_dir,
        rng=random.Random(1),
    )

    assert sorted(_lines(state_dir / "nau_playlist.tsv")) == sorted([
        f"{scripted_video}\t{mirrored}",
        f"{plain_video}",
    ])


# --- collect_video_files edge cases ---


def test_collect_video_files_skips_empty_source_parts(tmp_path: Path):
    d = tmp_path / "vids"
    d.mkdir()
    (d / "a.mp4").write_text("x", encoding="utf-8")
    spec = f"|{d}||"
    assert len(collect_video_files(spec)) == 1


def test_collect_video_files_ignores_non_video_files(tmp_path: Path):
    d = tmp_path / "vids"
    d.mkdir()
    (d / "clip.mp4").write_text("x", encoding="utf-8")
    (d / "notes.txt").write_text("x", encoding="utf-8")
    (d / "thumb.jpg").write_text("x", encoding="utf-8")
    assert collect_video_files(str(d)) == [str(d / "clip.mp4")]


def test_collect_video_files_deduplicates_across_source_parts(tmp_path: Path):
    d = tmp_path / "vids"
    d.mkdir()
    (d / "a.mp4").write_text("x", encoding="utf-8")
    spec = f"{d}|{d}"
    assert len(collect_video_files(spec)) == 1


def test_collect_video_files_accepts_single_file_path(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_text("x", encoding="utf-8")
    assert collect_video_files(str(f)) == [str(f)]


def test_collect_video_files_ignores_single_non_video_file(tmp_path: Path):
    f = tmp_path / "readme.txt"
    f.write_text("x", encoding="utf-8")
    assert collect_video_files(str(f)) == []


# --- build_mirrored_funscript_path edge cases ---


def test_build_mirrored_funscript_path_returns_empty_when_no_marker():
    assert build_mirrored_funscript_path(r"C:\other\path\clip.mp4") == ""


def test_has_matching_funscript_needs_only_the_video_path(tmp_path: Path):
    """The mirrored tree is derivable from the clip's own path, so nothing has to
    be told where the scripts live."""
    video = tmp_path / "videos" / "videos" / "2D" / "AI" / "portrait" / "clip.mp4"
    script = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "AI" / "portrait" / "clip.funscript"
    script.parent.mkdir(parents=True)
    script.write_text("script", encoding="utf-8")

    assert has_matching_funscript(str(video))
    assert not has_matching_funscript(r"C:\other\path\clip.mp4")


# --- read_favs_content / is_favorite_path edge cases ---


def test_read_favs_content_returns_empty_for_missing_file(tmp_path: Path):
    assert read_favs_content(tmp_path / "nope.csv") == ""


def test_is_favorite_path_returns_false_for_empty_inputs():
    assert is_favorite_path("", "some content") is False
    assert is_favorite_path("video.mp4", "") is False


# --- recency ordering ---


def test_sort_paths_by_recency_orders_newest_first(tmp_path: Path):
    d = tmp_path / "vids"
    old = d / "old.mp4"
    mid = d / "mid.mp4"
    new = d / "new.mp4"
    _touch_with_mtime(old, 1000)
    _touch_with_mtime(mid, 2000)
    _touch_with_mtime(new, 3000)

    result = sort_paths_by_recency([str(old), str(mid), str(new)])

    assert result == [str(new), str(mid), str(old)]


def test_order_paths_recent_orders_by_recency(tmp_path: Path):
    d = tmp_path / "vids"
    old = d / "old.mp4"
    new = d / "new.mp4"
    _touch_with_mtime(old, 1000)
    _touch_with_mtime(new, 2000)

    assert order_paths([str(old), str(new)], recent=True) == [str(new), str(old)]


def test_order_paths_not_recent_shuffles(tmp_path: Path):
    paths = [f"clip{i}.mp4" for i in range(12)]

    assert order_paths(list(paths), recent=False, rng=random.Random(7)) == shuffle_paths(
        list(paths), rng=random.Random(7)
    )


def test_build_satellite_playlist_paths_recent_orders_newest_first(tmp_path: Path):
    d = tmp_path / "portrait"
    old = d / "old.mp4"
    new = d / "new.mp4"
    _touch_with_mtime(old, 1000)
    _touch_with_mtime(new, 2000)

    paths = build_satellite_playlist_paths(str(d), False, tmp_path / "favs.csv", recent=True)

    assert paths == [str(new), str(old)]


def test_build_satellite_playlists_writes_both_recency_ordered_files(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    p_old, p_new = portrait_root / "p_old.mp4", portrait_root / "p_new.mp4"
    l_old, l_new = landscape_root / "l_old.mp4", landscape_root / "l_new.mp4"
    _touch_with_mtime(p_old, 1000)
    _touch_with_mtime(p_new, 2000)
    _touch_with_mtime(l_old, 1000)
    _touch_with_mtime(l_new, 2000)
    state_dir = tmp_path / "state"

    build_satellite_playlists(
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        portrait_recent=True,
        landscape_recent=True,
    )

    assert _lines(state_dir / "portrait_playlist.tsv") == [str(p_new), str(p_old)]
    assert _lines(state_dir / "landscape_playlist.tsv") == [str(l_new), str(l_old)]


def test_build_all_playlists_recent_orders_satellites(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    p_old, p_new = portrait_root / "p_old.mp4", portrait_root / "p_new.mp4"
    l_old, l_new = landscape_root / "l_old.mp4", landscape_root / "l_new.mp4"
    _touch_with_mtime(p_old, 1000)
    _touch_with_mtime(p_new, 2000)
    _touch_with_mtime(l_old, 1000)
    _touch_with_mtime(l_new, 2000)
    state_dir = tmp_path / "state"

    build_all_playlists(
        main_sources="",
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        portrait_recent=True,
        landscape_recent=True,
    )

    assert _lines(state_dir / "portrait_playlist.tsv") == [str(p_new), str(p_old)]
    assert _lines(state_dir / "landscape_playlist.tsv") == [str(l_new), str(l_old)]


# --- shuffle_paths edge cases ---


def test_shuffle_paths_returns_empty_list_unchanged():
    assert shuffle_paths([]) == []


def test_shuffle_paths_returns_single_item_unchanged():
    assert shuffle_paths(["only.mp4"]) == ["only.mp4"]


# --- f_mode=False branches ---


def test_build_primary_playlist_paths_returns_all_when_f_mode_false(tmp_path: Path):
    d = tmp_path / "vids"
    d.mkdir()
    (d / "a.mp4").write_text("x", encoding="utf-8")
    (d / "b.mp4").write_text("x", encoding="utf-8")
    paths = build_main_playlist_paths(str(d), False, rng=random.Random(1))
    assert len(paths) == 2


def test_build_satellite_playlist_paths_returns_all_when_f_mode_false(tmp_path: Path):
    d = tmp_path / "vids"
    d.mkdir()
    (d / "a.mp4").write_text("x", encoding="utf-8")
    (d / "b.mp4").write_text("x", encoding="utf-8")
    paths = build_satellite_playlist_paths(str(d), False, tmp_path / "favs.csv", rng=random.Random(1))
    assert len(paths) == 2


def test_build_primary_playlist_paths_includes_funscripted_ai_subdir_in_f_mode(tmp_path: Path):
    """F-mode main playlist should include AI videos with funscripts that live
    inside the main source tree (non_AI/actually_AI_but_funscripted/)."""
    primary_root = tmp_path / "videos" / "videos" / "2D" / "non_AI"
    non_ai_video = primary_root / "clip.mp4"
    ai_video = primary_root / "actually_AI_but_funscripted" / "portrait" / "funscripted_ai_clip.mp4"
    non_ai_video.parent.mkdir(parents=True)
    ai_video.parent.mkdir(parents=True)
    non_ai_video.write_text("x", encoding="utf-8")
    ai_video.write_text("x", encoding="utf-8")
    non_ai_script = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI" / "clip.funscript"
    ai_script = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI" / "actually_AI_but_funscripted" / "portrait" / "funscripted_ai_clip.funscript"
    non_ai_script.parent.mkdir(parents=True, exist_ok=True)
    ai_script.parent.mkdir(parents=True, exist_ok=True)
    non_ai_script.write_text("{}", encoding="utf-8")
    ai_script.write_text("{}", encoding="utf-8")

    paths = build_main_playlist_paths(str(primary_root), True, rng=random.Random(1))

    assert len(paths) == 2
    path_strs = {str(p) for p in paths}
    assert str(non_ai_video) in path_strs
    assert str(ai_video) in path_strs


# --- action-group collapse and watch weighting (satellites) ---


def test_shuffled_satellite_build_plays_one_member_per_action_group(tmp_path: Path):
    """Same subject+situation in several actions = one playlist slot, one random member."""
    source_dir, library, paths = _grouped_library(tmp_path, {
        "subject1_zeta": _i2v_meta("111", "Zeta Massage"),
        "subject1_alpha": _i2v_meta("111", "Alpha"),
        "subject1_epsilon": _i2v_meta("111", "Pov Epsilon"),
        "subject2_solo": _i2v_meta("222", "Dancing"),
        "no_metadata": None,
    })
    group = {paths["subject1_zeta"], paths["subject1_alpha"], paths["subject1_epsilon"]}

    seen: set[str] = set()
    for round_no in range(30):
        built = build_satellite_playlist_paths(
            str(source_dir), False, tmp_path / "favs.csv",
            rng=random.Random(round_no), library=library,
        )
        chosen = group.intersection(built)
        assert len(chosen) == 1, "exactly one action of the group per build"
        assert paths["subject2_solo"] in built
        assert paths["no_metadata"] in built
        seen |= chosen

    assert seen == group, "every action should get picked across builds"


def test_group_member_choice_follows_watch_weights(tmp_path: Path):
    from fun_time.watch_stats import record_watch_event

    source_dir, library, paths = _grouped_library(tmp_path, {
        "loved": _i2v_meta("111", "Zeta Massage"),
        "skipped": _i2v_meta("111", "Alpha"),
    })
    for _ in range(9):
        record_watch_event(library.watch_stats_file, paths["loved"], "completion")
        record_watch_event(library.watch_stats_file, paths["skipped"], "skip")

    loved_picks = sum(
        paths["loved"] in build_satellite_playlist_paths(
            str(source_dir), False, tmp_path / "favs.csv",
            rng=random.Random(round_no), library=library,
        )
        for round_no in range(40)
    )

    assert loved_picks >= 36, "the loved action should almost always represent its group"


def test_chronically_skipped_standalone_video_sits_most_builds_out(tmp_path: Path):
    from fun_time.watch_stats import record_watch_event

    source_dir, library, paths = _grouped_library(tmp_path, {
        "disliked": _i2v_meta("111", "Alpha"),
        "neutral": _i2v_meta("222", "Dancing"),
    })
    for _ in range(9):
        record_watch_event(library.watch_stats_file, paths["disliked"], "skip")

    appearances = sum(
        paths["disliked"] in build_satellite_playlist_paths(
            str(source_dir), False, tmp_path / "favs.csv",
            rng=random.Random(round_no), library=library,
        )
        for round_no in range(40)
    )

    assert appearances < 15, "a weight-1/8 video should miss most builds"


def test_latest_build_collapses_groups_to_newest_member(tmp_path: Path):
    """Latest shows one entry per action group even while reviewing arrivals:
    the group's newest member represents it, ungrouped clips pass through, and
    the whole list stays newest-first."""
    source_dir, library, paths = _grouped_library(tmp_path, {
        "subject1_old": _i2v_meta("111", "Alpha"),
        "subject1_new": _i2v_meta("111", "Zeta Massage"),
        "subject2_solo": _i2v_meta("222", "Dancing"),
        "no_metadata": None,
    })
    for name, mtime in (("subject1_old", 1000), ("subject2_solo", 2000), ("subject1_new", 3000), ("no_metadata", 500)):
        os.utime(Path(paths[name]), (mtime, mtime))

    built = build_satellite_playlist_paths(
        str(source_dir), False, tmp_path / "favs.csv",
        recent=True, rng=random.Random(1), library=library,
    )

    # subject1's two actions collapse to the newer one; order stays newest-first.
    assert built == [paths["subject1_new"], paths["subject2_solo"], paths["no_metadata"]]


def test_build_satellite_playlists_forwards_library_to_both_satellites(tmp_path: Path):
    source_dir, library, paths = _grouped_library(tmp_path, {
        "subject1_zeta": _i2v_meta("111", "Zeta Massage"),
        "subject1_alpha": _i2v_meta("111", "Alpha"),
    })
    state_dir = tmp_path / "state"

    build_satellite_playlists(
        portrait_sources=str(source_dir),
        landscape_sources=str(source_dir),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        portrait_recent=False,
        landscape_recent=False,
        rng=random.Random(5),
        library=library,
    )

    for name in ("portrait_playlist.tsv", "landscape_playlist.tsv"):
        listed = _lines(state_dir / name)
        assert len(listed) == 1, "the two-action group must collapse to one entry"
        assert listed[0] in paths.values()


def test_build_all_playlists_forwards_library_to_satellites(tmp_path: Path):
    source_dir, library, paths = _grouped_library(tmp_path, {
        "subject1_zeta": _i2v_meta("111", "Zeta Massage"),
        "subject1_alpha": _i2v_meta("111", "Alpha"),
    })
    primary_dir = tmp_path / "primary"
    primary_dir.mkdir()
    (primary_dir / "main.mp4").write_text("x", encoding="utf-8")

    build_all_playlists(
        main_sources=str(primary_dir),
        portrait_sources=str(source_dir),
        landscape_sources=str(source_dir),
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        rng=random.Random(5),
        library=library,
    )

    listed = _lines(tmp_path / "state" / "portrait_playlist.tsv")
    assert len(listed) == 1, "satellite collapse must apply on the F-mode/startup build"


# --- metadata attribute filtering ------------------------------------------

def test_satellite_filter_narrows_to_the_matching_action(tmp_path: Path):
    source_dir, library, paths = _grouped_library(tmp_path, {
        "clip": _i2v_meta("1", "Alpha"),
        "prone": _i2v_meta("2", "Beta Gamma"),
        "kiss": _i2v_meta("3", "Kissing"),
    })
    got = build_satellite_playlist_paths(
        str(source_dir), False, tmp_path / "favs.csv",
        filter_query="beta gamma", rng=random.Random(1), library=library,
    )
    assert got == [paths["prone"]]


def test_satellite_filter_drops_videos_without_a_sidecar(tmp_path: Path):
    source_dir, library, paths = _grouped_library(tmp_path, {
        "clip": _i2v_meta("1", "Alpha"),
        "nometa": None,
    })
    got = build_satellite_playlist_paths(
        str(source_dir), False, tmp_path / "favs.csv",
        filter_query="alpha", rng=random.Random(1), library=library,
    )
    assert got == [paths["clip"]]


def test_satellite_filter_composes_with_latest_ordering(tmp_path: Path):
    # Distinct prompts put the two Alphas in distinct seed families, so the
    # filtered build keeps both and the Latest newest-first ordering is visible.
    source_dir, library, paths = _grouped_library(tmp_path, {
        "old": _t2v_meta("Alpha", "1", prompt="scene one"),
        "new": _t2v_meta("Alpha", "2", prompt="scene two"),
        "other": _t2v_meta("Kissing", "3", prompt="scene three"),
    })
    os.utime(paths["old"], (1000, 1000))
    os.utime(paths["new"], (2000, 2000))
    got = build_satellite_playlist_paths(
        str(source_dir), False, tmp_path / "favs.csv",
        filter_query="alpha", recent=True, library=library,
    )
    assert got == [paths["new"], paths["old"]]  # filtered, newest-first


def test_satellite_filter_is_ignored_without_a_library(tmp_path: Path):
    # No metadata roots means the filter can't be evaluated, so nothing is dropped.
    d = tmp_path / "portrait"
    d.mkdir()
    (d / "a.mp4").write_text("x", encoding="utf-8")
    got = build_satellite_playlist_paths(
        str(d), False, tmp_path / "favs.csv", filter_query="alpha", rng=random.Random(1)
    )
    assert len(got) == 1


def test_build_satellite_playlists_applies_independent_per_satellite_filters(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    portrait_dir = media_root / "portrait"
    landscape_dir = media_root / "landscape"
    portrait_dir.mkdir(parents=True)
    landscape_dir.mkdir(parents=True)

    def make(folder: Path, name: str, action: str) -> str:
        video = folder / f"{name}.mp4"
        video.write_text("x", encoding="utf-8")
        sidecar = metadata_path_for(video, metadata_root)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"video": {"action": action, "prompt": "x", "seed": name}}),
            encoding="utf-8",
        )
        return str(video)

    p_cum, p_kiss = make(portrait_dir, "pc", "Alpha"), make(portrait_dir, "pk", "Kissing")
    l_cum, l_kiss = make(landscape_dir, "lc", "Alpha"), make(landscape_dir, "lk", "Kissing")
    library = SatelliteLibraryContext(
        metadata_root=metadata_root, watch_stats_file=tmp_path / "ws.json"
    )

    build_satellite_playlists(
        portrait_sources=str(portrait_dir),
        landscape_sources=str(landscape_dir),
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        portrait_recent=True,
        landscape_recent=True,
        portrait_filter="alpha",
        landscape_filter="kissing",
        library=library,
    )

    portrait_written = _lines(tmp_path / "state" / "portrait_playlist.tsv")
    landscape_written = _lines(tmp_path / "state" / "landscape_playlist.tsv")
    assert p_cum in portrait_written and p_kiss not in portrait_written
    assert l_kiss in landscape_written and l_cum not in landscape_written


# --- collapse axis: filtered views group by seed family (params) -------------

def _t2v_meta(action: str, seed: str, prompt: str = "same scene") -> dict:
    """A text-to-video sidecar: the action group pins the seed (action varies),
    while the seed family pins the action (seed varies)."""
    return {
        "video": {
            "prompt": prompt,
            "action": action,
            "seed": seed,
            "model": "Realism",
            "resolution": "720x560",
            "aspect_ratio": "9:7",
            "quality": "720p",
        }
    }


def test_filtered_build_collapses_same_params_different_seed(tmp_path: Path):
    """A filtered view has already pinned the act, so same-params-different-seed
    clips are one seed family and collapse to a single entry."""
    source_dir, library, paths = _grouped_library(tmp_path, {
        "clip_a": _t2v_meta("Alpha", "1"),
        "clip_b": _t2v_meta("Alpha", "2"),
    })
    os.utime(paths["clip_a"], (1000, 1000))
    os.utime(paths["clip_b"], (2000, 2000))

    built = build_satellite_playlist_paths(
        str(source_dir), False, tmp_path / "favs.csv",
        filter_query="alpha", recent=True, library=library,
    )

    assert built == [paths["clip_b"]]  # one per param-set, represented by its newest


def test_unfiltered_build_still_collapses_by_subject(tmp_path: Path):
    """Unfiltered browsing keeps today's one-clip-per-subject (action group),
    so two different-seed subjects both appear."""
    source_dir, library, paths = _grouped_library(tmp_path, {
        "clip_a": _t2v_meta("Alpha", "1"),
        "clip_b": _t2v_meta("Alpha", "2"),
    })
    os.utime(paths["clip_a"], (1000, 1000))
    os.utime(paths["clip_b"], (2000, 2000))

    built = build_satellite_playlist_paths(
        str(source_dir), False, tmp_path / "favs.csv", recent=True, library=library,
    )

    assert sorted(built) == sorted([paths["clip_a"], paths["clip_b"]])


def test_filtered_build_keeps_distinct_actions_apart(tmp_path: Path):
    """Seed-family collapse must not merge different acts: the t2v family pins
    the action, so a Kissing clip stays its own family."""
    source_dir, library, paths = _grouped_library(tmp_path, {
        "clip_a": _t2v_meta("Alpha", "1"),
        "clip_b": _t2v_meta("Alpha", "2"),
        "kiss": _t2v_meta("Kissing", "1"),
    })

    # A filter matching all three (their shared prompt) still collapses only
    # within a seed family, so Alpha collapses to one and Kissing survives.
    built = build_satellite_playlist_paths(
        str(source_dir), False, tmp_path / "favs.csv",
        filter_query="same scene", recent=True, library=library,
    )

    assert len(built) == 2
    assert paths["kiss"] in built


