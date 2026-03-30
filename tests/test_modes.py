from __future__ import annotations

import random
from pathlib import Path

from fun_time.modes import (
    build_fmode_playlists,
    build_mirrored_funscript_path,
    build_primary_playlist_paths,
    build_satellite_playlist_paths,
    collect_video_files,
    is_favorite_path,
    read_favs_content,
    shuffle_paths,
)


def test_build_mirrored_funscript_path_uses_primary_source_mirror(tmp_path: Path):
    source_root = tmp_path / "videos" / "videos" / "primary"
    video_path = source_root / "folder" / "clip.mp4"
    source_root.mkdir(parents=True)
    video_path.parent.mkdir(parents=True, exist_ok=True)

    result = build_mirrored_funscript_path(str(video_path), str(source_root))

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


def test_build_mirrored_funscript_path_skips_empty_and_missing_source_parts(tmp_path: Path):
    existing = tmp_path / "videos" / "videos" / "primary"
    existing.mkdir(parents=True)
    video = existing / "clip.mp4"
    spec = f"|{tmp_path / 'nonexistent'}|{existing}"
    result = build_mirrored_funscript_path(str(video), spec)
    assert "clip.funscript" in result


def test_build_mirrored_funscript_path_returns_empty_when_no_match(tmp_path: Path):
    d = tmp_path / "videos" / "videos" / "primary"
    d.mkdir(parents=True)
    unrelated = tmp_path / "other" / "clip.mp4"
    assert build_mirrored_funscript_path(str(unrelated), str(d)) == ""


# --- read_favs_content / is_favorite_path edge cases ---


def test_read_favs_content_returns_empty_for_missing_file(tmp_path: Path):
    assert read_favs_content(tmp_path / "nope.csv") == ""


def test_is_favorite_path_returns_false_for_empty_inputs():
    assert is_favorite_path("", "some content") is False
    assert is_favorite_path("video.mp4", "") is False


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


def test_build_primary_playlist_paths_includes_ai_sources_in_f_mode(tmp_path: Path):
    """F-mode primary playlist should include AI videos with funscripts."""
    primary_root = tmp_path / "videos" / "videos" / "2D" / "non_AI"
    ai_root = tmp_path / "videos" / "videos" / "2D" / "AI" / "2_outbox" / "upscaled_by_orientation" / "portrait"
    primary_root.mkdir(parents=True)
    ai_root.mkdir(parents=True)
    non_ai_video = primary_root / "non_ai_clip.mp4"
    ai_video = ai_root / "ai_clip.mp4"
    non_ai_video.write_text("x", encoding="utf-8")
    ai_video.write_text("x", encoding="utf-8")
    non_ai_script = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI" / "non_ai_clip.funscript"
    ai_script = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "AI" / "2_outbox" / "upscaled_by_orientation" / "portrait" / "ai_clip.funscript"
    non_ai_script.parent.mkdir(parents=True, exist_ok=True)
    ai_script.parent.mkdir(parents=True, exist_ok=True)
    non_ai_script.write_text("{}", encoding="utf-8")
    ai_script.write_text("{}", encoding="utf-8")
    all_sources = f"{primary_root}|{ai_root}"

    paths = build_primary_playlist_paths(
        str(primary_root), True, all_video_sources=all_sources, rng=random.Random(1),
    )

    assert len(paths) == 2
    path_strs = {str(p) for p in paths}
    assert str(non_ai_video) in path_strs
    assert str(ai_video) in path_strs


def test_build_fmode_playlists_includes_ai_funscripted_videos_in_primary(tmp_path: Path):
    """build_fmode_playlists should put AI funscripted videos into the primary playlist."""
    primary_root = tmp_path / "videos" / "videos" / "primary"
    portrait_root = tmp_path / "videos" / "videos" / "portrait"
    landscape_root = tmp_path / "videos" / "videos" / "landscape"
    for root in (primary_root, portrait_root, landscape_root):
        root.mkdir(parents=True)
    primary_video = primary_root / "main.mp4"
    portrait_video = portrait_root / "ai_portrait.mp4"
    primary_video.write_text("x", encoding="utf-8")
    portrait_video.write_text("x", encoding="utf-8")
    primary_script = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "main.funscript"
    portrait_script = tmp_path / "videos" / "scripts" / "scripts" / "portrait" / "ai_portrait.funscript"
    primary_script.parent.mkdir(parents=True, exist_ok=True)
    portrait_script.parent.mkdir(parents=True, exist_ok=True)
    primary_script.write_text("{}", encoding="utf-8")
    portrait_script.write_text("{}", encoding="utf-8")
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text("local_file,web_url\r\n", encoding="utf-8")
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

    assert plan.primary_count == 2
    playlist_content = (state_dir / "primary_vlc_playlist.m3u").read_text(encoding="utf-8")
    assert str(primary_video) in playlist_content
    assert str(portrait_video) in playlist_content


