from __future__ import annotations

import json
import os
from pathlib import Path

from main_funestra.gallery_names import GalleryNames

MADE = Path("C:/library/videos/wan_loop_00042_ (2)_topaz.mp4")


def _published(tmp_path: Path, names: dict[str, str]) -> Path:
    path = tmp_path / "gallery_names.json"
    path.write_text(json.dumps(names), encoding="utf-8")
    return path


def test_a_copy_the_library_made_of_a_video_wears_the_name_origenerator_gave_it(tmp_path):
    names = GalleryNames(_published(tmp_path, {"wan_loop_00042_": "3FA9C0D2 004"}))

    assert names.of(MADE) == "3FA9C0D2 004"


def test_a_copy_made_from_a_videos_sound_copy_wears_the_same_name(tmp_path):
    names = GalleryNames(_published(tmp_path, {"wan_loop_00042_": "3FA9C0D2 004"}))

    assert names.of(Path("C:/library/flicks/2D/AI/wan_loop_00042_-audio_topaz.mp4")) == (
        "3FA9C0D2 004")


def test_a_video_origenerator_never_named_has_no_gallery_name(tmp_path):
    names = GalleryNames(_published(tmp_path, {"wan_loop_00042_": "3FA9C0D2 004"}))

    assert names.of(Path("C:/library/videos/Jane Doe - scene one.mp4")) == ""


def test_with_nothing_published_no_video_has_a_gallery_name(tmp_path):
    assert GalleryNames(tmp_path / "gallery_names.json").of(MADE) == ""
    assert GalleryNames(None).of(MADE) == ""


def test_a_name_origenerator_gives_while_the_funestra_plays_is_worn_at_once(tmp_path):
    path = _published(tmp_path, {})
    names = GalleryNames(path)
    assert names.of(MADE) == ""

    _published(tmp_path, {"wan_loop_00042_": "3FA9C0D2 004"})
    os.utime(path, ns=(1, 1))

    assert names.of(MADE) == "3FA9C0D2 004"


def test_a_file_written_halfway_leaves_the_names_read_before_it(tmp_path):
    path = _published(tmp_path, {"wan_loop_00042_": "3FA9C0D2 004"})
    names = GalleryNames(path)
    assert names.of(MADE) == "3FA9C0D2 004"

    path.write_text('{"wan_loop_00042_": "3FA9', encoding="utf-8")
    os.utime(path, ns=(1, 1))

    assert names.of(MADE) == "3FA9C0D2 004"
