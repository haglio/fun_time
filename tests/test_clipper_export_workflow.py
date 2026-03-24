from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from fun_time.robot_hand.clipper.export import start_export_job, terminate_export_subprocesses
from fun_time.robot_hand.clipper.state import ExportJob

from tests.test_clipper_state import _make_state


def _run_export_worker_immediately(*, target, **_kwargs):
    fake_thread = MagicMock()
    target()
    return fake_thread


def test_start_export_job_returns_early_when_job_already_active():
    state = _make_state()
    active_job = ExportJob(active=True)
    state.export_job = active_job

    with patch("fun_time.robot_hand.clipper.export.start_daemon_thread") as start_thread:
        start_export_job(state)

    assert state.export_job is active_job
    start_thread.assert_not_called()


def test_start_export_job_completes_successful_pipeline(tmp_path: Path):
    state = _make_state(session_name="Demo Session")
    fake_thread = MagicMock()

    with patch("fun_time.robot_hand.clipper.export.RAW_CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.AUDIO_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.export_raw_clip", return_value=(True, "raw")) as export_raw, \
         patch("fun_time.robot_hand.clipper.export.run_clip_postprocess", return_value=(True, "clip")) as postprocess, \
         patch("fun_time.robot_hand.clipper.export.export_full_audio_mp3", return_value=(True, "audio")) as export_audio, \
         patch("fun_time.robot_hand.clipper.export.start_daemon_thread", side_effect=lambda **kwargs: _run_export_worker_immediately(**kwargs) or fake_thread):
        start_export_job(state)

    assert state.export_job is not None
    assert state.export_job.done is True
    assert state.export_job.active is False
    assert state.export_job.failed is False
    assert state.export_job.stage == "export complete"
    export_raw.assert_called_once()
    postprocess.assert_called_once()
    export_audio.assert_called_once()


def test_start_export_job_marks_raw_clip_failure(tmp_path: Path):
    state = _make_state()

    with patch("fun_time.robot_hand.clipper.export.RAW_CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.AUDIO_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.export_raw_clip", return_value=(False, "raw failed")) as export_raw, \
         patch("fun_time.robot_hand.clipper.export.run_clip_postprocess") as postprocess, \
         patch("fun_time.robot_hand.clipper.export.export_full_audio_mp3") as export_audio, \
         patch("fun_time.robot_hand.clipper.export.start_daemon_thread", side_effect=_run_export_worker_immediately):
        start_export_job(state)

    assert state.export_job is not None
    assert state.export_job.failed is True
    assert state.export_job.error_message == "raw failed"
    assert state.export_job.clip_status == "failed"
    assert state.export_job.done is True
    assert state.export_job.active is False
    export_raw.assert_called_once()
    postprocess.assert_not_called()
    export_audio.assert_not_called()


def test_start_export_job_marks_postprocess_failure(tmp_path: Path):
    state = _make_state()

    with patch("fun_time.robot_hand.clipper.export.RAW_CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.AUDIO_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.export_raw_clip", return_value=(True, "raw")) as export_raw, \
         patch("fun_time.robot_hand.clipper.export.run_clip_postprocess", return_value=(False, "post failed")) as postprocess, \
         patch("fun_time.robot_hand.clipper.export.export_full_audio_mp3") as export_audio, \
         patch("fun_time.robot_hand.clipper.export.start_daemon_thread", side_effect=_run_export_worker_immediately):
        start_export_job(state)

    assert state.export_job is not None
    assert state.export_job.failed is True
    assert state.export_job.error_message == "post failed"
    assert state.export_job.fix_status == "failed"
    assert state.export_job.done is True
    assert state.export_job.active is False
    export_raw.assert_called_once()
    postprocess.assert_called_once()
    export_audio.assert_not_called()


def test_start_export_job_marks_audio_failure(tmp_path: Path):
    state = _make_state()

    with patch("fun_time.robot_hand.clipper.export.RAW_CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.CLIPS_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.AUDIO_DIR", tmp_path), \
         patch("fun_time.robot_hand.clipper.export.export_raw_clip", return_value=(True, "raw")) as export_raw, \
         patch("fun_time.robot_hand.clipper.export.run_clip_postprocess", return_value=(True, "clip")) as postprocess, \
         patch("fun_time.robot_hand.clipper.export.export_full_audio_mp3", return_value=(False, "audio failed")) as export_audio, \
         patch("fun_time.robot_hand.clipper.export.start_daemon_thread", side_effect=_run_export_worker_immediately):
        start_export_job(state)

    assert state.export_job is not None
    assert state.export_job.failed is True
    assert state.export_job.error_message == "audio failed"
    assert state.export_job.audio_status == "failed"
    assert state.export_job.done is True
    assert state.export_job.active is False
    export_raw.assert_called_once()
    postprocess.assert_called_once()
    export_audio.assert_called_once()


def test_terminate_export_subprocesses_terminates_waits_and_clears():
    state = _make_state()
    running_proc = MagicMock()
    running_proc.poll.side_effect = [None, None]
    running_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=0.1)
    finished_proc = MagicMock()
    finished_proc.poll.return_value = 0

    state.export_job = ExportJob(procs=[running_proc, finished_proc])

    terminate_export_subprocesses(state)

    running_proc.terminate.assert_called_once()
    running_proc.kill.assert_called_once()
    finished_proc.terminate.assert_not_called()
    assert state.export_job.procs == []
