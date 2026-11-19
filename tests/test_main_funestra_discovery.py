from __future__ import annotations

from pathlib import Path

from fun_time.modes import scripted_item
from main_funestra.discovery import discover_entries
from main_funestra.library import LibraryEntry
from tests.disk_looks import DiskLooks


def _library(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "videos"
    return root / "videos", root / "scripts" / "scripts", root / "metadata"


def _write(path: Path, body: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestDiscoverEntries:
    def test_pairs_video_with_matching_funscript_and_size(self, tmp_path):
        videos, scripts, metadata = _library(tmp_path)
        video = _write(videos / "clip.mp4", "fake-body")
        script = _write(scripts / "clip.funscript", "{}")

        result = discover_entries(videos, metadata_root=metadata)

        assert result == [LibraryEntry(video=video, funscript=script, size=len("fake-body"))]

    def test_includes_video_without_funscript_with_none_script(self, tmp_path):
        videos, _scripts, metadata = _library(tmp_path)
        video = _write(videos / "clip.mp4")

        result = discover_entries(videos, metadata_root=metadata)

        assert [(entry.video, entry.funscript) for entry in result] == [(video, None)]

    def test_handles_subdirectories(self, tmp_path):
        videos, scripts, metadata = _library(tmp_path)
        _write(videos / "sub" / "deep.mkv")
        script = _write(scripts / "sub" / "deep.funscript", "{}")

        result = discover_entries(videos, metadata_root=metadata)

        assert [entry.funscript for entry in result] == [script]

    def test_empty_directory(self, tmp_path):
        videos, _scripts, metadata = _library(tmp_path)
        videos.mkdir(parents=True)

        assert discover_entries(videos, metadata_root=metadata) == []

    def test_result_is_stable_order(self, tmp_path):
        videos, _scripts, metadata = _library(tmp_path)
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            _write(videos / name)

        first = [entry.video for entry in discover_entries(videos, metadata_root=metadata)]
        second = [entry.video for entry in discover_entries(videos, metadata_root=metadata)]

        assert first == second

    def test_a_library_with_no_metadata_folder_named_pairs_beside_itself(self, tmp_path):
        videos, scripts, _metadata = _library(tmp_path)
        _write(videos / "clip.mp4")
        script = _write(scripts / "clip.funscript", "{}")

        result = discover_entries(videos, metadata_root=None)

        assert [entry.funscript for entry in result] == [script]

    def test_pairs_each_video_with_the_funscript_the_rooms_lists_give_it(self, tmp_path):
        videos, scripts, metadata = _library(tmp_path)
        walked = videos / "2D" / "non_AI"
        for name in ("scene one", "scene two"):
            _write(walked / "full" / f"{name}.mp4")
        _write(scripts / "2D" / "non_AI" / "full" / "scene one.funscript", "{}")

        result = discover_entries(walked, metadata_root=metadata)

        assert [entry.funscript for entry in result] == [
            scripted_item(str(entry.video), metadata).funscript for entry in result]
        assert [entry.funscript is not None for entry in result] == [True, False]

    def test_many_videos_in_one_folder_cost_two_listings_of_it_and_one_of_its_scripts(
            self, tmp_path, monkeypatch):
        videos, scripts, metadata = _library(tmp_path)
        for number in range(5):
            _write(videos / "room" / f"video {number}.mp4", "fake-body")
        _write(scripts / "room" / "video 2.funscript", "{}")
        looks = DiskLooks(monkeypatch, under=tmp_path / "videos")

        result = discover_entries(videos, metadata_root=metadata)

        assert ([entry.funscript is not None for entry in result], {entry.size for entry in result},
                looks.total) == ([False, False, True, False, False], {len("fake-body")}, 4)
