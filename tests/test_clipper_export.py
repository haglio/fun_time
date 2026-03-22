"""Tests for fun_time.robot_hand.clipper.export (pure-logic and lightly-mocked)."""
from __future__ import annotations

import io
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import numpy as np

import pytest

from fun_time.robot_hand.clipper.export import (
    _parse_ffmpeg_clock,
    _run_ffmpeg_with_progress,
    run_loop_fix,
    validate_video_file,
)
from fun_time.robot_hand.clipper.state import ExportJob
from tests.test_clipper_state import _make_state


# ---------------------------------------------------------------------------
# _parse_ffmpeg_clock
# ---------------------------------------------------------------------------

class TestParseFfmpegClock:
    def test_zero(self):
        assert _parse_ffmpeg_clock("00:00:00.000000") == pytest.approx(0.0)

    def test_seconds(self):
        assert _parse_ffmpeg_clock("00:00:30.000000") == pytest.approx(30.0)

    def test_minutes(self):
        assert _parse_ffmpeg_clock("00:01:00.000000") == pytest.approx(60.0)

    def test_hours(self):
        assert _parse_ffmpeg_clock("01:00:00.000000") == pytest.approx(3600.0)

    def test_combined(self):
        # 1h 2m 3.5s
        assert _parse_ffmpeg_clock("01:02:03.500000") == pytest.approx(3723.5)

    def test_invalid_returns_zero(self):
        assert _parse_ffmpeg_clock("N/A") == 0.0

    def test_empty_returns_zero(self):
        assert _parse_ffmpeg_clock("") == 0.0

    def test_garbage_returns_zero(self):
        assert _parse_ffmpeg_clock("not:a:number") == 0.0


# ---------------------------------------------------------------------------
# validate_video_file
# ---------------------------------------------------------------------------

class TestValidateVideoFile:
    def test_nonexistent_file_returns_false(self, tmp_path: Path):
        ok, msg = validate_video_file(tmp_path / "ghost.mp4")
        assert ok is False
        assert "not created" in msg.lower() or "exist" in msg.lower()

    def test_tiny_file_returns_false(self, tmp_path: Path):
        tiny = tmp_path / "tiny.mp4"
        tiny.write_bytes(b"\x00" * 100)  # less than 2048 bytes
        ok, msg = validate_video_file(tiny)
        assert ok is False
        assert "tiny" in msg.lower()

    def test_unreadable_cv2_file_returns_false(self, tmp_path: Path):
        fake = tmp_path / "fake.mp4"
        fake.write_bytes(b"\x00" * 4096)  # big enough bytes-wise but invalid video

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cap.read.return_value = (False, None)

        with patch("fun_time.robot_hand.clipper.export.cv2.VideoCapture", return_value=mock_cap):
            ok, msg = validate_video_file(fake)

        assert ok is False
        assert "unreadable" in msg.lower() or "locked" in msg.lower()

    def test_no_readable_frames_returns_false(self, tmp_path: Path):
        fake = tmp_path / "fake.mp4"
        fake.write_bytes(b"\x00" * 4096)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (False, None)

        with patch("fun_time.robot_hand.clipper.export.cv2.VideoCapture", return_value=mock_cap):
            ok, msg = validate_video_file(fake)

        assert ok is False
        assert "no readable frames" in msg.lower()

    def test_valid_file_returns_true(self, tmp_path: Path):
        fake = tmp_path / "ok.mp4"
        fake.write_bytes(b"\x00" * 4096)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        mock_cap.read.return_value = (True, frame)

        with patch("fun_time.robot_hand.clipper.export.cv2.VideoCapture", return_value=mock_cap):
            ok, msg = validate_video_file(fake)

        assert ok is True
        assert msg == ""

    def test_cap_released_on_success(self, tmp_path: Path):
        fake = tmp_path / "ok.mp4"
        fake.write_bytes(b"\x00" * 4096)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, np.zeros((2, 2, 3), dtype=np.uint8))

        with patch("fun_time.robot_hand.clipper.export.cv2.VideoCapture", return_value=mock_cap):
            validate_video_file(fake)

        mock_cap.release.assert_called_once()

    def test_cap_released_on_failure(self, tmp_path: Path):
        fake = tmp_path / "ok.mp4"
        fake.write_bytes(b"\x00" * 4096)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False

        with patch("fun_time.robot_hand.clipper.export.cv2.VideoCapture", return_value=mock_cap):
            validate_video_file(fake)

        mock_cap.release.assert_called_once()


# ---------------------------------------------------------------------------
# _run_ffmpeg_with_progress  (mocked subprocess)
# ---------------------------------------------------------------------------

class TestRunFfmpegWithProgress:
    def _make_proc_mock(self, output_lines: list[str], returncode: int = 0) -> MagicMock:
        proc = MagicMock()
        proc.stdout = io.StringIO("\n".join(output_lines))
        proc.wait.return_value = returncode
        proc.poll.return_value = returncode
        return proc

    def test_success_calls_set_progress_to_1(self):
        proc = self._make_proc_mock(["progress=end"])
        progress_values: list[float] = []

        with patch("subprocess.Popen", return_value=proc):
            ok, err = _run_ffmpeg_with_progress(
                ["ffmpeg", "-version"], 10.0, progress_values.append
            )

        assert ok is True
        assert progress_values[-1] == pytest.approx(1.0)

    def test_nonzero_exit_returns_false(self):
        proc = self._make_proc_mock([], returncode=1)
        progress_values: list[float] = []

        with patch("subprocess.Popen", return_value=proc):
            ok, err = _run_ffmpeg_with_progress(
                ["ffmpeg", "-version"], 10.0, progress_values.append
            )

        assert ok is False
        assert "1" in err

    def test_launch_failure_returns_false(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError("not found")):
            ok, err = _run_ffmpeg_with_progress(
                ["ffmpeg"], 10.0, lambda p: None
            )
        assert ok is False
        assert "not found" in err

    def test_out_time_line_advances_progress(self):
        # 5 seconds out of 10 total → 0.5
        proc = self._make_proc_mock(["out_time=00:00:05.000000", "progress=end"])
        recorded: list[float] = []

        with patch("subprocess.Popen", return_value=proc):
            _run_ffmpeg_with_progress(["ffmpeg"], 10.0, recorded.append)

        # At some point 0.5 should have been reported
        assert any(abs(v - 0.5) < 0.01 for v in recorded)

    def test_job_proc_removed_after_run(self):
        proc = self._make_proc_mock(["progress=end"])
        job = ExportJob()

        with patch("subprocess.Popen", return_value=proc):
            _run_ffmpeg_with_progress(["ffmpeg"], 5.0, lambda p: None, job=job)

        assert proc not in job.procs

    def test_progress_never_exceeds_1(self):
        # Simulate an out_time that exceeds total duration
        proc = self._make_proc_mock(["out_time=99:00:00.000000", "progress=end"])
        recorded: list[float] = []

        with patch("subprocess.Popen", return_value=proc):
            _run_ffmpeg_with_progress(["ffmpeg"], 1.0, recorded.append)

        assert all(v <= 1.0 for v in recorded)


class TestRunLoopFix:
    def test_passes_loop_mode_to_script(self, tmp_path: Path):
        job = ExportJob()
        state = _make_state(loop_mode="tip-base")
        raw_path = tmp_path / "raw.mp4"
        out_path = tmp_path / "out.mp4"

        proc = MagicMock()
        proc.stdout = io.StringIO("done\n")
        proc.poll.side_effect = [0]
        proc.wait.return_value = 0

        with patch("fun_time.robot_hand.clipper.export.LOOP_FIX_SCRIPT", tmp_path / "loop_fix.py"):
            (tmp_path / "loop_fix.py").write_text("# test\n", encoding="utf-8")
            with patch("subprocess.Popen", return_value=proc) as popen:
                ok, detail = run_loop_fix(state, raw_path, out_path, job)

        assert ok is True
        assert detail == str(out_path)
        cmd = popen.call_args.args[0]
        assert "--loop-mode" in cmd
        assert "tip-base" in cmd
