from __future__ import annotations

import subprocess
import time

from .export_steps import export_full_audio_mp3, export_raw_clip, run_clip_postprocess
from .paths import AUDIO_DIR, CLIPS_DIR, RAW_CLIPS_DIR
from .state import ExportJob, VideoState
from .utils import sanitize_name
from ...threading_utils import start_daemon_thread


def _mark_export_failure(job: ExportJob, detail: str, status_attr: str) -> None:
    job.failed = True
    job.error_message = detail
    setattr(job, status_attr, "Failed")
    job.active = False
    job.done = True


def _run_export_pipeline(state: VideoState, job: ExportJob) -> None:
    session_base = sanitize_name(state.session_name)
    raw_path = RAW_CLIPS_DIR / f"{session_base}.mp4"
    clip_path = CLIPS_DIR / f"{session_base}.mp4"
    audio_path = AUDIO_DIR / f"{session_base}.mp3"
    ok, detail = export_raw_clip(state, raw_path, job)
    if not ok:
        _mark_export_failure(job, detail, "clip_status")
        return
    ok, detail = run_clip_postprocess(state, raw_path, clip_path, job)
    if not ok:
        _mark_export_failure(job, detail, "fix_status")
        return
    ok, detail = export_full_audio_mp3(state, audio_path, job)
    if not ok:
        _mark_export_failure(job, detail, "audio_status")
        return
    job.stage = "Export complete"
    job.done = True
    job.active = False


def start_export_job(state: VideoState) -> None:
    if state.export_job and state.export_job.active:
        return
    job = ExportJob(active=True, stage="Preparing export")
    state.export_job = job

    def worker() -> None:
        _run_export_pipeline(state, job)

    worker_thread = start_daemon_thread(target=worker)
    job.worker = worker_thread


def terminate_export_subprocesses(state: VideoState) -> None:
    job = state.export_job
    if not job:
        return
    for proc in list(job.procs):
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    deadline = time.time() + 1.0
    for proc in list(job.procs):
        try:
            if proc.poll() is None:
                remaining = max(0.0, deadline - time.time())
                proc.wait(timeout=remaining)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    job.procs.clear()
