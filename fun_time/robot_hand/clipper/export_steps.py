from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from collections.abc import Callable, Sequence

import cv2

from .paths import CLIP_POSTPROCESS_SCRIPT
from .state import ExportJob, VideoState
from .utils import find_tool, subprocess_window_kwargs


def _parse_ffmpeg_clock(s: str) -> float:
    try:
        hh, mm, ss = s.split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        return 0.0


def _run_ffmpeg_with_progress(
    cmd: Sequence[str],
    total_duration: float,
    set_progress: Callable[[float], None],
    job: ExportJob | None = None,
) -> tuple[bool, str]:
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **subprocess_window_kwargs(),
        )
        if job is not None:
            job.procs.append(proc)
    except Exception as exc:
        return False, str(exc)
    progress = 0.0
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            partial = None
            if key == "out_time":
                partial = _parse_ffmpeg_clock(value) / max(1e-9, total_duration)
            elif key == "progress" and value == "end":
                partial = 1.0
            if partial is not None:
                progress = max(progress, min(1.0, partial))
                set_progress(progress)
        rc = proc.wait()
    finally:
        if job is not None and proc in job.procs:
            try:
                job.procs.remove(proc)
            except ValueError:
                pass
        if proc.stdout is not None:
            proc.stdout.close()
    if rc != 0:
        return False, f"ffmpeg exited with code {rc}"
    set_progress(1.0)
    return True, ""


def validate_video_file(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "Output file was not created"
    if path.stat().st_size < 2048:
        return False, "Output file is suspiciously tiny. Another program may have the source video locked. Close that program and retry."
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return False, "Output video is unreadable. Another program may have the source video locked. Close that program and retry."
        ok, _ = cap.read()
        if not ok:
            return False, "Output video contains no readable frames. Another program may have the source video locked. Close that program and retry."
    finally:
        cap.release()
    return True, ""


def export_raw_clip(state: VideoState, out_path: Path, job: ExportJob) -> tuple[bool, str]:
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg not found on PATH"
    clip_duration = max(1.0 / state.fps, (state.active_end - state.active_start + 1) / state.fps)
    start_sec = state.active_start / state.fps
    end_sec = (state.active_end + 1) / state.fps
    # Seek near the target so ffmpeg jumps to a nearby keyframe. Then trim using
    # timestamps relative to the seeked input segment.
    seek_sec = max(0.0, start_sec - 5.0)
    trim_start_rel = max(0.0, start_sec - seek_sec)
    trim_end_rel = trim_start_rel + max(1.0 / state.fps, end_sec - start_sec)
    vf = f"trim=start={trim_start_rel:.6f}:end={trim_end_rel:.6f},setpts=PTS-STARTPTS"
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-stats_period", "0.1",
        "-ss", f"{seek_sec:.6f}", "-i", state.path, "-map", "0:v:0", "-vf", vf, "-r", f"{state.fps:.12g}", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path),
    ]
    job.stage = "exporting raw silent clip"
    job.clip_status = "encoding"
    ok, detail = _run_ffmpeg_with_progress(cmd, clip_duration, lambda p: setattr(job, "clip_progress", p), job)
    if not ok:
        return False, detail
    job.clip_status = "finalizing..."
    ok2, detail2 = validate_video_file(out_path)
    if not ok2:
        return False, detail2
    job.clip_status = "done"
    job.raw_clip_output = str(out_path)
    return True, str(out_path)


def run_clip_postprocess(state: VideoState, raw_path: Path, out_path: Path, job: ExportJob) -> tuple[bool, str]:
    job.stage = f"running {CLIP_POSTPROCESS_SCRIPT.name}"
    if not CLIP_POSTPROCESS_SCRIPT.exists():
        return False, f"{CLIP_POSTPROCESS_SCRIPT.name} not found at {CLIP_POSTPROCESS_SCRIPT}"
    cmd = [sys.executable, str(CLIP_POSTPROCESS_SCRIPT), str(raw_path), "-o", str(out_path), "--loop-mode", state.loop_mode]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **subprocess_window_kwargs())
        job.procs.append(proc)
    except Exception as exc:
        return False, str(exc)
    lines = []
    while True:
        line = proc.stdout.readline() if proc.stdout else ""
        if line:
            lines.append(line.rstrip())
        if proc.poll() is not None:
            break
        job.fix_progress = min(0.95, job.fix_progress + 0.01)
        job.fix_status = "working"
        time.sleep(0.1)
    if proc.stdout:
        rest = proc.stdout.read()
        if rest:
            lines.append(rest)
        proc.stdout.close()
    if proc in job.procs:
        try:
            job.procs.remove(proc)
        except ValueError:
            pass
    rc = proc.wait()
    if rc != 0:
        return False, f"{CLIP_POSTPROCESS_SCRIPT.name} failed:\n" + "\n".join(lines[-20:])
    job.fix_progress = 1.0
    job.fix_status = "done"
    job.clip_output = str(out_path)
    return True, str(out_path)


run_loop_fix = run_clip_postprocess


def export_full_audio_mp3(state: VideoState, out_path: Path, job: ExportJob) -> tuple[bool, str]:
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg not found on PATH"
    full_duration = max(1.0 / state.fps, state.total_frames / state.fps)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-stats_period", "0.1",
        "-i", state.path, "-vn", "-map", "0:a:0?", "-c:a", "libmp3lame", "-q:a", "2", str(out_path),
    ]
    job.stage = "extracting full audio to mp3"
    job.audio_status = "encoding"
    ok, detail = _run_ffmpeg_with_progress(cmd, full_duration, lambda p: setattr(job, "audio_progress", p), job)
    if not ok:
        return False, detail
    if not out_path.exists() or out_path.stat().st_size == 0:
        return False, "No MP3 output created"
    job.audio_status = "done"
    job.audio_output = str(out_path)
    return True, str(out_path)
