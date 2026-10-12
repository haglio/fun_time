from __future__ import annotations

import json
from pathlib import Path

import pytest

import main_funestra.library_source
from main_funestra.library import LibraryEntry
from main_funestra.library_source import PHASE_DISCOVER, LibrarySource, build_library_source


def _make_video(path: Path, body: str = "x") -> Path:
    path.write_text(body)
    return path


class TestBuildLibrarySource:
    def test_version_index_covers_all_entries(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        big = _make_video(vids / "Jane-1080p.mp4", body="a bigger body here")
        small = _make_video(vids / "Jane-540.mp4", body="sm")

        source = build_library_source(vids, scripts)

        # Both versions map to the same ordered pair list (canonical first).
        assert source.version_index[big] == source.version_index[small]
        assert source.version_index[big][0][0] == big


class TestTheVersionIndexIsBuiltOnce:
    """It looks like an attribute and was a plain property, so every reader paid
    a full walk of the library and one JSON parse per video.

    Startup alone has two readers -- `main_funestra.cli.resolve_playlist` collapses Fun
    Time's playlist to one entry per version family, then `main_funestra.app` hands the
    session its index -- and startup latency is the thing the loading screen
    exists to paper over.  Nothing reads it after that: the session is handed a
    plain dict and holds it for the rest of the run, so the answer was already
    fixed for the session and the second walk only spent time.
    """

    def test_reading_it_twice_walks_the_sidecars_once(self, tmp_path, monkeypatch):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        entries = []
        for name in ("one", "two", "three"):
            video = lib / f"{name}.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(b"x")
            entries.append(LibraryEntry(video=video, funscript=None, size=10))
        reads: list[Path] = []
        real = main_funestra.library_source.recorded_for
        monkeypatch.setattr(
            main_funestra.library_source, "recorded_for",
            lambda video, root: (reads.append(video), real(video, root))[1])
        source = LibrarySource(entries=entries, metadata_root=meta)

        first = source.version_index
        second = source.version_index

        assert len(reads) == 3, "one walk, not one per reader"
        assert first == second
        assert second is first


class TestBuildProgress:
    """Walking the library is the wait the loading screen covers, so the build
    reports it."""

    def test_reports_the_walk(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        _make_video(vids / "a.mp4")
        seen: list[str] = []

        build_library_source(vids, scripts, on_progress=seen.append)

        assert seen == [PHASE_DISCOVER]

    def test_a_raising_callback_stops_the_build(self, tmp_path):
        """Closing the window mid-wait has to end the wait, not be noticed once
        it is over — so the callback aborts the build by raising."""
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()

        class GaveUp(Exception):
            """Whatever the caller raises — the build has no opinion on it."""

        def give_up(_phase):
            raise GaveUp

        with pytest.raises(GaveUp):
            build_library_source(vids, scripts, on_progress=give_up)


def test_version_index_groups_by_metadata_sidecar_when_metadata_root_set(tmp_path):

    lib = tmp_path / "videos" / "videos"
    meta = tmp_path / "videos" / "metadata"
    original = lib / "example" / "2_orig" / "Scene-One-abc.mkv"
    upscale = lib / "example" / "3_done" / "wholly-different-name_apo8_iris2.mp4"
    for clip, size in ((original, 100), (upscale, 900)):
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"x")
        side = (meta / clip.relative_to(lib)).with_suffix(".json")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(
            json.dumps({"version": {"group": "example/Scene-One-abc", "processed": clip is upscale}}),
            encoding="utf-8",
        )
    ea = LibraryEntry(video=original, funscript=None, size=100)
    eb = LibraryEntry(video=upscale, funscript=None, size=900)
    source = LibrarySource(entries=[ea, eb], metadata_root=meta)

    index = source.version_index

    assert index[original] == index[upscale]  # folded despite unrelated names
    assert index[original][0][0] == upscale  # canonical is the larger clip


def test_version_index_falls_back_to_names_without_a_metadata_root(tmp_path):
    a = LibraryEntry(video=Path("Richard.mp4"), funscript=None, size=50)
    b = LibraryEntry(video=Path("Richard_topaz.mp4"), funscript=None, size=800)
    source = LibrarySource(entries=[a, b])

    assert source.version_index[a.video] == source.version_index[b.video]
