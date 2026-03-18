from __future__ import annotations

import random
import subprocess
from pathlib import Path

from PIL import Image, ImageOps, ImageTk

SUPPORTED_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


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
    out = subprocess.check_output(cmd, text=True).strip()
    width, height = out.split("x", 1)
    return int(width), int(height)


def decode_video_to_pil_frames(path: Path) -> list[Image.Image]:
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

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames: list[Image.Image] = []

    try:
        while True:
            buf = proc.stdout.read(frame_size) if proc.stdout else b""
            if not buf:
                break
            if len(buf) != frame_size:
                break
            frames.append(Image.frombytes("RGB", (width, height), buf))
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


def make_photo(img: Image.Image, max_width: int, max_height: int):
    sized = ImageOps.contain(img, (max(1, int(max_width)), max(1, int(max_height))), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(sized)