from __future__ import annotations

import json
from pathlib import Path

from main_player.sidecar import (
    read_clip,
    read_sidecar,
    read_version_group,
    read_video_type,
    sidecar_for,
)


def _write(lib: Path, meta: Path, rel: str, payload: dict) -> Path:
    video = lib / rel
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")
    side = (meta / rel).with_suffix(".json")
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(payload), encoding="utf-8")
    return video


class TestSidecarFor:
    def test_mirrors_the_librarys_own_layout(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"

        assert sidecar_for(lib / "w" / "x.mp4", meta) == meta / "w" / "x.json"

    def test_a_genau_loop_mirrors_from_beside_the_library(self):
        """Genau's clips sit next to the video tree rather than inside it, and
        Evolver files their records the same way."""
        meta = Path("D:/suite/videos/metadata")

        assert sidecar_for(Path("D:/suite/videos/genau/clips/loop.mp4"), meta) == (
            meta / "genau" / "clips" / "loop.json"
        )

    def test_a_video_outside_the_library_has_none(self, tmp_path):
        meta = tmp_path / "videos" / "metadata"

        assert sidecar_for(Path("D:/elsewhere/x.mp4"), meta) is None


class TestReadSidecar:
    def test_a_missing_file_reads_as_nothing_recorded(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        (lib / "w").mkdir(parents=True)

        assert read_sidecar(lib / "w" / "x.mp4", meta) == {}

    def test_malformed_json_reads_as_nothing_recorded(self, tmp_path):
        """Plenty of videos predate the metadata, and Evolver rewrites these
        files as it runs — an unreadable one is a video the main player knows less about,
        never a crash."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        video = _write(lib, meta, "w/x.mp4", {})
        (meta / "w" / "x.json").write_text("{ truncated", encoding="utf-8")

        assert read_sidecar(video, meta) == {}


class TestReadClip:
    def test_reads_the_clip_object(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        video = _write(lib, meta, "w/Ann Bly - Alpha Scene 2.mp4", {
            "video": {"action": "Alpha"},
            "clip": {"compilation": "Vol6", "index": 9, "performer": "Ann Bly"},
        })

        assert read_clip(video, meta) == {
            "compilation": "Vol6", "index": 9, "performer": "Ann Bly",
        }

    def test_a_sidecar_with_no_clip_object_is_none(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        video = _write(lib, meta, "w/y.mp4", {"version": {"group": "w/y"}})

        assert read_clip(video, meta) is None


class TestReadVideoType:
    def test_reads_the_kind_evolver_recorded(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        video = _write(lib, meta, "w/x.mp4", {"video": {"type": "excerpt"}})

        assert read_video_type(video, meta) == "excerpt"

    def test_a_video_with_no_record_reads_as_nothing(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        video = _write(lib, meta, "w/y.mp4", {"version": {"group": "y"}})

        assert read_video_type(video, meta) == ""


class TestReadVersionGroup:
    def test_reads_the_family_id(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        video = _write(lib, meta, "w/y_apo8_iris2.mp4", {"version": {"group": "y", "processed": True}})

        assert read_version_group(video, meta) == "y"

    def test_a_sidecar_with_no_version_object_is_none(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        video = _write(lib, meta, "w/y.mp4", {"clip": {"compilation": "Vol6"}})

        assert read_version_group(video, meta) is None
