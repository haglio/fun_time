from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

import main_player.library_source
from main_player.duration_cache import DurationCache
from main_player.library import FULL, MIXED, SHORTS, LibraryEntry
from main_player.library_source import (
    DEFAULT_MODE,
    PHASE_DISCOVER,
    PHASE_DURATIONS,
    LibrarySource,
    build_library_source,
    discover_genau_clips,
    length_mode_rebuilds,
    next_length_mode,
)


class TestLengthModeCycle:
    def test_the_player_opens_unfiltered(self):
        """Fun Time's own playlist has always been every video shuffled, so a
        player that opened claiming a length was claiming one it did not have."""
        assert DEFAULT_MODE == MIXED

    def test_the_toggle_walks_all_three_and_comes_back(self):
        assert next_length_mode(MIXED) == SHORTS
        assert next_length_mode(SHORTS) == FULL
        assert next_length_mode(FULL) == MIXED

    def test_an_unknown_mode_lands_on_the_default(self):
        """Nothing sets one, but the toggle must land somewhere real rather than
        raise into the run loop."""
        assert next_length_mode("") == DEFAULT_MODE


class TestLengthModeRebuilds:
    """Naming a length reshuffles the playlist and lands on entry 0, so asking
    for one that is already running is not free — it changes the video."""

    def test_a_different_mode_always_rebuilds(self):
        assert length_mode_rebuilds(SHORTS, MIXED, in_compilation=False) is True
        assert length_mode_rebuilds(MIXED, FULL, in_compilation=True) is True

    def test_the_mode_already_running_asks_for_nothing(self):
        """Fun Time's reset says "mixed" on every press, and each rebuild landed
        on a different video — so a control meaning "put it back" was the
        quickest way to keep changing what played."""
        assert length_mode_rebuilds(MIXED, MIXED, in_compilation=False) is False

    def test_inside_a_compilation_the_same_mode_is_the_way_out(self):
        """PLAY_COMPILATION swaps the playlist for one volume's clips and leaves
        the mode alone, so naming that same length is what leaves it."""
        assert length_mode_rebuilds(MIXED, MIXED, in_compilation=True) is True


def _make_video(path: Path, body: str = "x") -> Path:
    path.write_text(body)
    return path


class TestBuildLibrarySource:
    def test_playlist_for_full_and_shorts(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        long_vid = _make_video(vids / "long-1080p.mp4")
        short_vid = _make_video(vids / "teaser-1080p.mp4")
        durations = {long_vid: 300.0, short_vid: 10.0}

        source = build_library_source(
            vids, scripts, None, rng=random.Random(0), durations=durations,
        )

        full = source.playlist_for("full")
        shorts = source.playlist_for("shorts")
        assert [v for v, _ in full] == [long_vid]
        assert [v for v, _ in shorts] == [short_vid]

    def test_shorts_mode_includes_clips_dir(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        clips = tmp_path / "clips"
        vids.mkdir()
        scripts.mkdir()
        clips.mkdir()
        long_vid = _make_video(vids / "long-1080p.mp4")
        clip = _make_video(clips / "saved.mp4")
        durations = {long_vid: 300.0}

        source = build_library_source(
            vids, scripts, clips, rng=random.Random(0), durations=durations,
        )

        shorts_videos = {v for v, _ in source.playlist_for("shorts")}
        assert clip in shorts_videos

    def test_version_index_covers_all_entries(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        big = _make_video(vids / "Jane-1080p.mp4", body="a bigger body here")
        small = _make_video(vids / "Jane-540.mp4", body="sm")
        durations = {big: 300.0, small: 300.0}

        source = build_library_source(
            vids, scripts, None, rng=random.Random(0), durations=durations,
        )

        # Both versions map to the same ordered pair list (canonical first).
        assert source.version_index[big] == source.version_index[small]
        assert source.version_index[big][0][0] == big

    def test_version_index_includes_clips(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        clips = tmp_path / "clips"
        vids.mkdir()
        scripts.mkdir()
        clips.mkdir()
        long_vid = _make_video(vids / "long-1080p.mp4")
        clip = _make_video(clips / "saved.mp4")
        durations = {long_vid: 300.0}

        source = build_library_source(
            vids, scripts, clips, rng=random.Random(0), durations=durations,
        )

        assert clip in source.version_index


class TestTheVersionIndexIsBuiltOnce:
    """It looks like an attribute and was a plain property, so every reader paid
    a full walk of the library and one JSON parse per video.

    Startup alone has two readers -- `main_player.cli.resolve_playlist` collapses Fun
    Time's playlist to one entry per version family, then `main_player.app` hands the
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
        real = main_player.library_source.read_version_group
        monkeypatch.setattr(
            main_player.library_source, "read_version_group",
            lambda video, root: (reads.append(video), real(video, root))[1])
        source = LibrarySource(
            entries=entries, genau_clips=[], durations={}, rng=random.Random(0),
            metadata_root=meta,
        )

        first = source.version_index
        second = source.version_index

        assert len(reads) == 3, "one walk, not one per reader"
        assert first == second
        assert second is first

class TestBuildProgress:
    """Startup's only long wait is probing durations, so the build reports it."""

    def test_reports_each_probed_entry_against_the_total(self, tmp_path):
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            _make_video(vids / name)
        seen: list[tuple[str, int, int]] = []

        build_library_source(
            vids, scripts, None, rng=random.Random(0),
            duration_cache=DurationCache(tmp_path / "cache.json", prober=lambda p: 300.0),
            on_progress=lambda phase, done, total: seen.append((phase, done, total)),
        )

        assert (PHASE_DISCOVER, 0, 0) in seen
        assert [s for s in seen if s[0] == PHASE_DURATIONS] == [
            (PHASE_DURATIONS, 0, 3), (PHASE_DURATIONS, 1, 3), (PHASE_DURATIONS, 2, 3),
        ]

    def test_a_video_whose_kind_is_on_file_is_never_measured(self, tmp_path):
        """The probe is startup's whole wait, and a recorded kind removes the
        reason for it — a backfilled library opens without one ffprobe."""
        vids = tmp_path / "videos" / "videos"
        scripts = tmp_path / "scripts"
        metadata = tmp_path / "videos" / "metadata"
        vids.mkdir(parents=True)
        scripts.mkdir()
        metadata.mkdir(parents=True)
        _make_video(vids / "recorded.mp4")
        _make_video(vids / "unrecorded.mp4")
        (metadata / "recorded.json").write_text(
            json.dumps({"video": {"type": "full_length"}}), encoding="utf-8",
        )
        probed: list[Path] = []

        source = build_library_source(
            vids, scripts, None, rng=random.Random(0), metadata_root=metadata,
            duration_cache=DurationCache(
                tmp_path / "cache.json", prober=lambda p: probed.append(p) or 300.0,
            ),
        )

        assert [p.name for p in probed] == ["unrecorded.mp4"]
        assert vids / "recorded.mp4" not in source.durations

    def test_a_raising_callback_stops_the_probing(self, tmp_path):
        """Closing the window mid-probe has to end the wait, not be noticed once
        it is over — so the callback aborts the build by raising."""
        vids = tmp_path / "videos"
        scripts = tmp_path / "scripts"
        vids.mkdir()
        scripts.mkdir()
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            _make_video(vids / name)
        probed: list[Path] = []

        class GaveUp(Exception):
            """Whatever the caller raises — the build has no opinion on it."""

        def give_up(phase, done, total):
            if phase == PHASE_DURATIONS and done == 1:
                raise GaveUp

        with pytest.raises(GaveUp):
            build_library_source(
                vids, scripts, None, rng=random.Random(0),
                duration_cache=DurationCache(
                    tmp_path / "cache.json", prober=lambda p: probed.append(p) or 300.0,
                ),
                on_progress=give_up,
            )

        assert len(probed) == 1  # stopped at the raise, not after all three


class TestDiscoverClips:
    def test_absent_dir_is_empty(self, tmp_path):
        assert discover_genau_clips(tmp_path / "nope") == []

    def test_none_dir_is_empty(self):
        assert discover_genau_clips(None) == []

    def test_lists_clip_videos_with_size(self, tmp_path):
        clips = tmp_path / "clips"
        clips.mkdir()
        (clips / "a.mp4").write_text("body")
        (clips / "notes.txt").write_text("ignore me")

        result = discover_genau_clips(clips)

        assert len(result) == 1
        assert result[0].video == clips / "a.mp4"
        assert result[0].funscript is None
        assert result[0].size == len("body")


def test_standalone_source_serves_all_videos_by_default():
    """Standalone main player is a general player; scripted-focus is Fun Time's
    F-mode, so the default source serves scripted and unscripted alike."""
    from pathlib import Path


    scripted = LibraryEntry(video=Path("Gigi-topaz.mp4"), funscript=Path("Gigi.funscript"), size=900)
    unscripted = LibraryEntry(video=Path("Hana-1080p.mp4"), funscript=None, size=900)
    src = LibrarySource(
        entries=[scripted, unscripted], genau_clips=[],
        durations={scripted.video: 300.0, unscripted.video: 300.0},
        rng=random.Random(0),
    )

    vids = {v for v, _ in src.playlist_for("full")}

    assert scripted.video in vids and unscripted.video in vids


def test_version_index_groups_by_metadata_sidecar_when_metadata_root_set(tmp_path):
    import json


    lib = tmp_path / "videos" / "videos"
    meta = tmp_path / "videos" / "metadata"
    original = lib / "larkin" / "2_orig" / "Scene-One-abc.mkv"
    upscale = lib / "larkin" / "3_done" / "wholly-different-name_apo8_iris2.mp4"
    for clip, size in ((original, 100), (upscale, 900)):
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"x")
        side = (meta / clip.relative_to(lib)).with_suffix(".json")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(
            json.dumps({"version": {"group": "larkin/Scene-One-abc", "processed": clip is upscale}}),
            encoding="utf-8",
        )
    ea = LibraryEntry(video=original, funscript=None, size=100)
    eb = LibraryEntry(video=upscale, funscript=None, size=900)
    source = LibrarySource(
        entries=[ea, eb], genau_clips=[], durations={original: 300.0, upscale: 300.0},
        rng=random.Random(0), metadata_root=meta,
    )

    index = source.version_index

    assert index[original] == index[upscale]  # folded despite unrelated names
    assert index[original][0][0] == upscale  # canonical is the larger clip


def test_version_index_falls_back_to_names_without_a_metadata_root(tmp_path):


    a = LibraryEntry(video=Path("Richard.mp4"), funscript=None, size=50)
    b = LibraryEntry(video=Path("Richard_topaz.mp4"), funscript=None, size=800)
    source = LibrarySource(
        entries=[a, b], genau_clips=[], durations={a.video: 300.0, b.video: 300.0},
        rng=random.Random(0),
    )

    assert source.version_index[a.video] == source.version_index[b.video]


class TestACarvedSceneIsAShort:
    """Length is not the only thing that makes a short.

    A scene Evolver carved out of a compilation is a short however long it
    runs, and the ``clip`` record in its sidecar was how that was known before
    there was a kind to write — so a library Evolver has not been over since
    still has only that, and it is still read.
    """

    @staticmethod
    def _clip_entry(lib: Path, meta: Path, rel: str) -> LibraryEntry:
        video = lib / rel
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"x")
        side = (meta / video.relative_to(lib)).with_suffix(".json")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(json.dumps({"clip": {"compilation": "Volume Six", "index": 9,
                                             "source": "alpha scene two",
                                             "performer": "Jane Doe"}}))
        return LibraryEntry(video=video, funscript=None, size=100)

    @staticmethod
    def _plain_entry(lib: Path, rel: str) -> LibraryEntry:
        video = lib / rel
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"x")
        return LibraryEntry(video=video, funscript=None, size=100)

    def test_it_surfaces_as_a_short_and_never_as_full_length(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = self._clip_entry(lib, meta, "example/1 clips/Jane Doe - alpha scene two.mp4")
        plain = self._plain_entry(lib, "example/0/Long Movie.mp4")
        source = LibrarySource(
            entries=[clip, plain], genau_clips=[],
            # Both well over the short cutoff, so only the sidecar can tell them apart.
            durations={clip.video: 120.0, plain.video: 120.0},
            rng=random.Random(0), metadata_root=meta,
        )

        shorts = {v for v, _ in source.playlist_for(SHORTS)}
        full = {v for v, _ in source.playlist_for(FULL)}

        assert clip.video in shorts
        assert clip.video not in full
        assert plain.video in full
        assert plain.video not in shorts

    def test_without_a_metadata_root_the_length_is_all_there_is(self, tmp_path):
        lib = tmp_path / "videos" / "videos"
        long_plain = self._plain_entry(lib, "example/Long.mp4")
        source = LibrarySource(
            entries=[long_plain], genau_clips=[], durations={long_plain.video: 120.0},
            rng=random.Random(0),
        )

        assert long_plain.video in {v for v, _ in source.playlist_for(FULL)}
        assert long_plain.video not in {v for v, _ in source.playlist_for(SHORTS)}
