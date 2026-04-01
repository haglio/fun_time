from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import numpy as np

from ..runtime_support import hidden_subprocess_kwargs

SUPPORTED_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def _subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    return hidden_subprocess_kwargs()


def scan_clips(folder: Path, *, shuffle_on_load: bool = True) -> list[Path]:
    files = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTS]
    if not files:
        raise RuntimeError(f"No video clips found in: {folder}")
    if shuffle_on_load:
        random.shuffle(files)
    return files


def ffprobe_size(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True, **_subprocess_kwargs()).strip()
    width, height = out.split("x", 1)
    return int(width), int(height)


def decode_video_to_numpy_frames(path: Path) -> list[np.ndarray]:
    width, height = ffprobe_size(path)
    frame_size = width * height * 3

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-vsync",
        "0",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_subprocess_kwargs())
    frames: list[np.ndarray] = []

    try:
        while True:
            buf = proc.stdout.read(frame_size) if proc.stdout else b""
            if not buf:
                break
            if len(buf) != frame_size:
                break
            frames.append(
                np.frombuffer(buf, dtype=np.uint8).reshape((height, width, 3)).copy()
            )
    finally:
        if proc.stdout:
            proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(stderr.strip() or f"ffmpeg failed for {path}")

    if not frames:
        raise RuntimeError(f"No frames decoded from: {path}")

    return frames


def cache_dir_for_clips_folder(folder: Path) -> Path:
    return folder / ".rhcache"


def load_clip_frames(video_path: Path, cache_dir: Path) -> list[np.ndarray]:
    from .frame_cache import read_rhcache_all_frames

    cache_path = cache_dir / (video_path.stem + ".rhcache")
    if cache_path.exists():
        return read_rhcache_all_frames(cache_path)

    return decode_video_to_numpy_frames(video_path)
