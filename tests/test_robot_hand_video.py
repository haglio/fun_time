"""Tests for fun_time.robot_hand.video."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fun_time.robot_hand.video import SUPPORTED_VIDEO_EXTS, scan_clips


# ---------------------------------------------------------------------------
# SUPPORTED_VIDEO_EXTS
# ---------------------------------------------------------------------------

class TestSupportedVideoExts:
    def test_contains_mp4(self):
        assert ".mp4" in SUPPORTED_VIDEO_EXTS

    def test_contains_mkv(self):
        assert ".mkv" in SUPPORTED_VIDEO_EXTS

    def test_contains_mov(self):
        assert ".mov" in SUPPORTED_VIDEO_EXTS

    def test_contains_avi(self):
        assert ".avi" in SUPPORTED_VIDEO_EXTS

    def test_contains_webm(self):
        assert ".webm" in SUPPORTED_VIDEO_EXTS

    def test_contains_m4v(self):
        assert ".m4v" in SUPPORTED_VIDEO_EXTS

    def test_all_lowercase(self):
        for ext in SUPPORTED_VIDEO_EXTS:
            assert ext == ext.lower(), f"{ext!r} should be lowercase"


# ---------------------------------------------------------------------------
# scan_clips
# ---------------------------------------------------------------------------

class TestScanClips:
    def test_finds_mp4_files(self, tmp_path: Path):
        (tmp_path / "a.mp4").touch()
        (tmp_path / "b.mp4").touch()
        result = scan_clips(tmp_path, shuffle_on_load=False)
        names = {p.name for p in result}
        assert names == {"a.mp4", "b.mp4"}

    def test_finds_mixed_extensions(self, tmp_path: Path):
        (tmp_path / "movie.mkv").touch()
        (tmp_path / "clip.mp4").touch()
        result = scan_clips(tmp_path, shuffle_on_load=False)
        assert len(result) == 2

    def test_ignores_non_video_files(self, tmp_path: Path):
        (tmp_path / "video.mp4").touch()
        (tmp_path / "notes.txt").touch()
        (tmp_path / "image.jpg").touch()
        result = scan_clips(tmp_path, shuffle_on_load=False)
        assert len(result) == 1
        assert result[0].name == "video.mp4"

    def test_ignores_subdirectories(self, tmp_path: Path):
        (tmp_path / "video.mp4").touch()
        (tmp_path / "subdir").mkdir()
        result = scan_clips(tmp_path, shuffle_on_load=False)
        assert len(result) == 1

    def test_raises_when_folder_empty(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="No video clips found"):
            scan_clips(tmp_path)

    def test_raises_when_only_non_video_files(self, tmp_path: Path):
        (tmp_path / "readme.txt").touch()
        with pytest.raises(RuntimeError, match="No video clips found"):
            scan_clips(tmp_path)

    def test_returns_list(self, tmp_path: Path):
        (tmp_path / "clip.mp4").touch()
        result = scan_clips(tmp_path)
        assert isinstance(result, list)

    def test_returns_path_objects(self, tmp_path: Path):
        (tmp_path / "clip.mp4").touch()
        result = scan_clips(tmp_path)
        for item in result:
            assert isinstance(item, Path)

    def test_extension_matching_is_case_insensitive(self, tmp_path: Path):
        (tmp_path / "clip.MP4").touch()
        (tmp_path / "other.MKV").touch()
        result = scan_clips(tmp_path, shuffle_on_load=False)
        assert len(result) == 2

    def test_shuffle_off_gives_deterministic_order(self, tmp_path: Path):
        for name in ["c.mp4", "a.mp4", "b.mp4"]:
            (tmp_path / name).touch()
        r1 = scan_clips(tmp_path, shuffle_on_load=False)
        r2 = scan_clips(tmp_path, shuffle_on_load=False)
        assert r1 == r2

    def test_shuffle_on_returns_all_files(self, tmp_path: Path):
        for name in ["x.mp4", "y.mp4", "z.mp4"]:
            (tmp_path / name).touch()
        result = scan_clips(tmp_path, shuffle_on_load=True)
        assert len(result) == 3
        assert {p.name for p in result} == {"x.mp4", "y.mp4", "z.mp4"}
