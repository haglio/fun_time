from __future__ import annotations

import random
from pathlib import Path

from fun_time.modes import (
    build_fmode_playlists,
    build_mirrored_funscript_path,
    build_primary_playlist_paths,
    build_satellite_playlist_paths,
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


