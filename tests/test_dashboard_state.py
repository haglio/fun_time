from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_state import (
    build_mirrored_funscript_path,
    has_matching_funscript,
    is_favorite_path,
    read_favs_content,
)


def test_build_mirrored_funscript_path_maps_videos_tree_into_scripts_tree(tmp_path: Path):
    video_path = tmp_path / "videos" / "videos" / "2D" / "non_AI" / "demo.mp4"

    mirrored = build_mirrored_funscript_path(str(video_path))

    expected = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI" / "demo.funscript"
    assert Path(mirrored) == expected


def test_has_matching_funscript_checks_mirrored_path(tmp_path: Path):
    video_path = tmp_path / "videos" / "videos" / "2D" / "non_AI" / "demo.mp4"
    funscript_path = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI" / "demo.funscript"
    funscript_path.parent.mkdir(parents=True)
    funscript_path.write_text("script", encoding="utf-8")

    assert has_matching_funscript(str(video_path))


def test_has_matching_funscript_returns_false_without_marker():
    assert not has_matching_funscript(r"C:\other\path\clip.mp4")


def test_build_mirrored_funscript_path_maps_ai_tree(tmp_path: Path):
    video_path = tmp_path / "videos" / "videos" / "2D" / "AI" / "2_outbox" / "upscaled_by_orientation" / "portrait" / "clip.mp4"

    mirrored = build_mirrored_funscript_path(str(video_path))

    expected = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "AI" / "2_outbox" / "upscaled_by_orientation" / "portrait" / "clip.funscript"
    assert Path(mirrored) == expected


def test_has_matching_funscript_needs_only_video_path(tmp_path: Path):
    """Funscript detection should work from the video path alone, no source roots needed."""
    ai_video = tmp_path / "videos" / "videos" / "2D" / "AI" / "portrait" / "clip.mp4"
    ai_funscript = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "AI" / "portrait" / "clip.funscript"
    ai_video.parent.mkdir(parents=True)
    ai_video.write_text("video", encoding="utf-8")
    ai_funscript.parent.mkdir(parents=True)
    ai_funscript.write_text("script", encoding="utf-8")

    assert has_matching_funscript(str(ai_video))


def test_read_favs_content_and_is_favorite_path_round_trip(tmp_path: Path):
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text("local_file,web_url\nC:\\clips\\portrait.mp4,\n", encoding="utf-8")

    favs_content = read_favs_content(favs_file)

    assert "portrait.mp4" in favs_content
    assert is_favorite_path(r"C:\clips\portrait.mp4", favs_content)
    assert not is_favorite_path(r"C:\clips\landscape.mp4", favs_content)
