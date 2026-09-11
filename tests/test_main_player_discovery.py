from __future__ import annotations

from main_player.discovery import discover_entries
from main_player.library import LibraryEntry


class TestDiscoverEntries:
    def test_pairs_video_with_matching_funscript_and_size(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        (vids / "clip.mp4").write_text("fake-body")
        (scripts / "clip.funscript").write_text("{}")

        result = discover_entries(vids, scripts)

        assert len(result) == 1
        entry = result[0]
        assert isinstance(entry, LibraryEntry)
        assert entry.video == vids / "clip.mp4"
        assert entry.funscript == scripts / "clip.funscript"
        assert entry.size == len("fake-body")

    def test_includes_video_without_funscript_with_none_script(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        (vids / "clip.mp4").write_text("fake")

        result = discover_entries(vids, scripts)

        assert len(result) == 1
        assert result[0].video == vids / "clip.mp4"
        assert result[0].funscript is None

    def test_handles_subdirectories(self, tmp_path):
        vids = tmp_path / "videos" / "sub"
        scripts = tmp_path / "scripts" / "sub"
        vids.mkdir(parents=True)
        scripts.mkdir(parents=True)
        (vids / "deep.mkv").write_text("fake")
        (scripts / "deep.funscript").write_text("{}")

        result = discover_entries(vids.parent, scripts.parent)

        assert len(result) == 1
        assert result[0].funscript == scripts / "deep.funscript"

    def test_empty_directory(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()

        assert discover_entries(vids, scripts) == []

    def test_result_is_stable_order(self, tmp_path):
        # Discovery no longer shuffles; the library layer owns randomization.
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            (vids / name).write_text("x")

        first = [e.video for e in discover_entries(vids, scripts)]
        second = [e.video for e in discover_entries(vids, scripts)]
        assert first == second
