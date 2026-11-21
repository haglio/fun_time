from __future__ import annotations

import os
from pathlib import Path

from fun_time.media_metadata import is_favorite
from fun_time.modes import (
    build_mirrored_funscript_path,
    collect_video_files,
    matching_funscript,
    rotated_onto,
    sort_paths_by_recency,
    write_playlist_file,
)
from tests.disk_looks import DiskLooks
from tests.drive_fakes import files_that_never_answer


def _lines(playlist: Path) -> list[str]:
    return playlist.read_text(encoding="utf-8").splitlines()


def _touch_with_mtime(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_build_mirrored_funscript_path_uses_video_path_directly(tmp_path: Path):
    video_path = tmp_path / "videos" / "videos" / "primary" / "folder" / "clip.mp4"

    result = build_mirrored_funscript_path(str(video_path))

    assert result == str(tmp_path / "videos" / "scripts" / "scripts" / "primary" / "folder" / "clip.funscript")


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


def test_collect_video_files_passes_over_a_video_still_being_written(tmp_path: Path):
    d = tmp_path / "vids"
    d.mkdir()
    (d / "clip.mp4").write_text("x", encoding="utf-8")
    (d / "clip.partial.0123abcd.mp4").write_text("x", encoding="utf-8")
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


def test_collect_video_files_reads_the_folder_and_asks_no_file_about_itself(tmp_path: Path):
    d = tmp_path / "vids"
    (d / "deeper").mkdir(parents=True)
    (d / "clip.mp4").write_text("x", encoding="utf-8")
    (d / "deeper" / "other.mkv").write_text("x", encoding="utf-8")
    (d / "notes.txt").write_text("x", encoding="utf-8")

    with files_that_never_answer(d):
        found = collect_video_files(str(d))

    assert sorted(found) == [str(d / "clip.mp4"), str(d / "deeper" / "other.mkv")]


def test_a_video_outside_the_mirrored_tree_names_no_funscript():
    assert build_mirrored_funscript_path(r"C:\other\path\clip.mp4") == ""


def test_a_video_off_the_library_has_no_funscript(tmp_path: Path):
    assert matching_funscript(r"C:\other\path\clip.mp4") is None


def test_a_video_kept_off_the_librarys_drive_pairs_with_the_librarys_own_script(tmp_path: Path):
    library = tmp_path / "library" / "videos"
    kept = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished" / "scene one.mp4"
    script = library / "scripts" / "scripts" / "VR" / "finished" / "scene one.funscript"
    script.parent.mkdir(parents=True)
    script.write_text("{}", encoding="utf-8")

    assert matching_funscript(str(kept), metadata_root=library / "metadata") == str(script)


def test_a_satellite_screens_playlist_pairs_each_video_with_its_funscript(tmp_path: Path):
    library = tmp_path / "videos" / "videos" / "portrait"
    scripted, plain = library / "scripted.mp4", library / "plain.mp4"
    script = tmp_path / "videos" / "scripts" / "scripts" / "portrait" / "scripted.funscript"
    script.parent.mkdir(parents=True)
    script.write_text("{}", encoding="utf-8")
    playlist = tmp_path / "portrait_playlist.tsv"

    write_playlist_file(playlist, [str(scripted), str(plain)])

    assert _lines(playlist) == [f"{scripted}\t{script}", str(plain)]


def test_a_playlist_of_many_videos_in_one_folder_looks_in_the_scripts_folder_once(
        tmp_path: Path, monkeypatch):
    library = tmp_path / "videos" / "videos" / "portrait"
    scripts = tmp_path / "videos" / "scripts" / "scripts" / "portrait"
    scripts.mkdir(parents=True)
    (scripts / "video 2.funscript").write_text("{}", encoding="utf-8")
    videos = [library / f"video {number}.mp4" for number in range(5)]
    looks = DiskLooks(monkeypatch, under=tmp_path / "videos" / "scripts")

    write_playlist_file(tmp_path / "portrait_playlist.tsv", [str(video) for video in videos])

    assert (_lines(tmp_path / "portrait_playlist.tsv")[2], looks.total) == (
        f"{videos[2]}\t{scripts / 'video 2.funscript'}", 1)


def test_the_main_funestras_playlist_pairs_a_kept_video_with_the_librarys_script(tmp_path: Path):
    library = tmp_path / "library" / "videos"
    kept = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished" / "scene one.mp4"
    script = library / "scripts" / "scripts" / "VR" / "finished" / "scene one.funscript"
    script.parent.mkdir(parents=True)
    script.write_text("{}", encoding="utf-8")
    playlist = tmp_path / "main_player_playlist.txt"

    write_playlist_file(playlist, [str(kept)], metadata_root=library / "metadata")

    assert str(script) in _lines(playlist)[0]


def test_a_video_with_no_record_is_no_favorite(tmp_path: Path):
    assert not is_favorite(tmp_path / "videos" / "videos" / "scene one.mp4", tmp_path / "videos" / "metadata")


def test_with_no_records_folder_no_video_is_a_favorite(tmp_path: Path):
    assert not is_favorite(tmp_path / "videos" / "videos" / "scene one.mp4", None)


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


def test_sort_paths_by_recency_reads_the_dates_off_the_folder(tmp_path: Path):
    d = tmp_path / "vids"
    old = d / "old.mp4"
    new = d / "new.mp4"
    _touch_with_mtime(old, 1000)
    _touch_with_mtime(new, 3000)

    with files_that_never_answer(d):
        result = sort_paths_by_recency([str(old), str(new)])

    assert result == [str(new), str(old)]


def test_a_queue_is_rotated_so_the_video_the_funestra_left_on_leads():
    assert rotated_onto(["a.mp4", "b.mp4", "c.mp4"], "b.mp4", path_of=str) == ["b.mp4", "c.mp4", "a.mp4"]


def test_a_queue_not_holding_the_video_comes_back_from_its_top():
    assert rotated_onto(["a.mp4", "b.mp4"], "gone.mp4", path_of=str) == ["a.mp4", "b.mp4"]


def test_a_funestra_that_published_no_status_leaves_the_queue_as_it_was():
    assert rotated_onto(["a.mp4", "b.mp4"], "", path_of=str) == ["a.mp4", "b.mp4"]


