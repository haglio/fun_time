from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_state import (
    build_mirrored_funscript_path,
    clip_label_from_path,
    has_matching_funscript,
    is_favorite_path,
    primary_panel_should_highlight,
    read_favs_content,
    satellite_panel_should_highlight,
)


def test_primary_panel_highlight_follows_f_mode_or_funscript():
    assert primary_panel_should_highlight(
        f_mode_enabled=True,
        primary_path="",
        has_matching_funscript=False,
    )
    assert primary_panel_should_highlight(
        f_mode_enabled=False,
        primary_path="clip.mp4",
        has_matching_funscript=True,
    )
    assert not primary_panel_should_highlight(
        f_mode_enabled=False,
        primary_path="clip.mp4",
        has_matching_funscript=False,
    )


def test_satellite_panel_highlight_follows_f_mode_or_favorite():
    assert satellite_panel_should_highlight(f_mode_enabled=True, is_favorite=False)
    assert satellite_panel_should_highlight(f_mode_enabled=False, is_favorite=True)
    assert not satellite_panel_should_highlight(f_mode_enabled=False, is_favorite=False)


def test_clip_label_from_path_uses_filename_or_none():
    assert clip_label_from_path("") == "(none)"
    assert clip_label_from_path(r"C:\clips\demo-primary.mp4") == "demo-primary.mp4"


def test_build_mirrored_funscript_path_maps_videos_tree_into_scripts_tree(tmp_path: Path):
    source_root = tmp_path / "videos" / "videos" / "2D" / "non_AI"
    video_path = source_root / "demo.mp4"
    source_root.mkdir(parents=True)
    video_path.write_text("video", encoding="utf-8")

    mirrored = build_mirrored_funscript_path(str(video_path), str(source_root))

    expected = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI" / "demo.funscript"
    assert Path(mirrored) == expected


def test_has_matching_funscript_uses_mirrored_primary_sources_tree(tmp_path: Path):
    source_root = tmp_path / "videos" / "videos" / "2D" / "non_AI"
    video_path = source_root / "demo.mp4"
    funscript_path = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI" / "demo.funscript"
    source_root.mkdir(parents=True)
    video_path.write_text("video", encoding="utf-8")
    funscript_path.parent.mkdir(parents=True)
    funscript_path.write_text("script", encoding="utf-8")

    assert has_matching_funscript(str(video_path), str(source_root))
    assert not has_matching_funscript(str(video_path), str(tmp_path / "other"))


def test_read_favs_content_and_is_favorite_path_round_trip(tmp_path: Path):
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text("local_file,web_url\nC:\\clips\\portrait.mp4,\n", encoding="utf-8")

    favs_content = read_favs_content(favs_file)

    assert "portrait.mp4" in favs_content
    assert is_favorite_path(r"C:\clips\portrait.mp4", favs_content)
    assert not is_favorite_path(r"C:\clips\landscape.mp4", favs_content)
