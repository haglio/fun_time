from __future__ import annotations

import os
import random
from pathlib import Path

from fun_time.modes import (
    build_fmode_playlists,
    build_mirrored_funscript_path,
    build_primary_playlist_paths,
    build_satellite_playlist_paths,
    build_satellite_playlists,
    collect_video_files,
    is_favorite_path,
    order_paths,
    read_favs_content,
    shuffle_paths,
    sort_paths_by_recency,
)


def _touch_with_mtime(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


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

    paths = build_primary_playlist_paths(str(source_root), True, rng=random.Random(1))

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


def test_build_fmode_playlists_writes_named_m3u_files(tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    for root in (primary_root, portrait_root, landscape_root):
        root.mkdir(parents=True)
    primary_video = primary_root / "main.mp4"
    portrait_video = portrait_root / "portrait.mp4"
    landscape_video = landscape_root / "landscape.mp4"
    for path in (primary_video, portrait_video, landscape_video):
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

    plan = build_fmode_playlists(
        primary_sources=str(primary_root),
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=favs_file,
        state_dir=state_dir,
        enabled=True,
        rng=random.Random(1),
    )

    assert plan.success is True
    assert plan.primary_count == 1
    assert plan.portrait_count == 1
    assert plan.landscape_count == 1
    assert (state_dir / "primary_vlc_playlist.m3u").read_text(encoding="utf-8").startswith("#EXTM3U")
    assert str(primary_video) in (state_dir / "primary_vlc_playlist.m3u").read_text(encoding="utf-8")


def test_build_fmode_playlists_writes_nau_playlist_with_funscript_pairs(tmp_path: Path):
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

    plan = build_fmode_playlists(
        primary_sources=str(primary_root),
        portrait_sources="",
        landscape_sources="",
        favs_file=favs_file,
        state_dir=state_dir,
        enabled=False,
        rng=random.Random(1),
    )

    assert plan.nau_playlist_path == state_dir / "nau_playlist.tsv"
    lines = plan.nau_playlist_path.read_text(encoding="utf-8").strip().splitlines()
    assert sorted(lines) == sorted([
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

    plan = build_satellite_playlists(
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        f_mode=False,
        recent=True,
    )

    assert plan.portrait_count == 2
    assert plan.landscape_count == 2
    assert plan.portrait_playlist_path == state_dir / "portrait_vlc_playlist.m3u"
    assert plan.landscape_playlist_path == state_dir / "landscape_vlc_playlist.m3u"
    portrait_lines = plan.portrait_playlist_path.read_text(encoding="utf-8").splitlines()
    landscape_lines = plan.landscape_playlist_path.read_text(encoding="utf-8").splitlines()
    assert portrait_lines[1:] == [str(p_new), str(p_old)]
    assert landscape_lines[1:] == [str(l_new), str(l_old)]


def test_build_fmode_playlists_recent_orders_satellites(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    p_old, p_new = portrait_root / "p_old.mp4", portrait_root / "p_new.mp4"
    l_old, l_new = landscape_root / "l_old.mp4", landscape_root / "l_new.mp4"
    _touch_with_mtime(p_old, 1000)
    _touch_with_mtime(p_new, 2000)
    _touch_with_mtime(l_old, 1000)
    _touch_with_mtime(l_new, 2000)
    state_dir = tmp_path / "state"

    plan = build_fmode_playlists(
        primary_sources="",
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        enabled=False,
        recent=True,
    )

    portrait_lines = plan.portrait_playlist_path.read_text(encoding="utf-8").splitlines()
    landscape_lines = plan.landscape_playlist_path.read_text(encoding="utf-8").splitlines()
    assert portrait_lines[1:] == [str(p_new), str(p_old)]
    assert landscape_lines[1:] == [str(l_new), str(l_old)]


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
    paths = build_primary_playlist_paths(str(d), False, rng=random.Random(1))
    assert len(paths) == 2


def test_build_satellite_playlist_paths_returns_all_when_f_mode_false(tmp_path: Path):
    d = tmp_path / "vids"
    d.mkdir()
    (d / "a.mp4").write_text("x", encoding="utf-8")
    (d / "b.mp4").write_text("x", encoding="utf-8")
    paths = build_satellite_playlist_paths(str(d), False, tmp_path / "favs.csv", rng=random.Random(1))
    assert len(paths) == 2


def test_build_primary_playlist_paths_includes_funscripted_ai_subdir_in_f_mode(tmp_path: Path):
    """F-mode primary playlist should include AI videos with funscripts that live
    inside the primary source tree (non_AI/actually_AI_but_funscripted/)."""
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

    paths = build_primary_playlist_paths(str(primary_root), True, rng=random.Random(1))

    assert len(paths) == 2
    path_strs = {str(p) for p in paths}
    assert str(non_ai_video) in path_strs
    assert str(ai_video) in path_strs


